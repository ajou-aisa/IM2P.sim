#!/usr/bin/env python3
"""Framing/identity control tests; PTY replies are explicitly non-numerical."""
import os
from pathlib import Path
import pty
import struct
import threading
import tempfile
import unittest
from unittest.mock import patch
import zlib
import protocol
from measure import check_host


def reply(identity=17, generation=0):
    body=bytearray(96)
    struct.pack_into('<4sBBHQII',body,0,b'OFR1',1,0,protocol.PROFILE,identity,generation,0)
    return bytes(body)+struct.pack('<I',zlib.crc32(body))


class Framing(unittest.TestCase):
    def test_measurement_requires_numerical_backend_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            log=Path(directory)/'host.log'
            log.write_text('exit zero without completion\n')
            with self.assertRaises(RuntimeError):check_host(log,'FPGA_REPLAY_V1',0)
            log.write_text('FPGA_REPLAY_V1 PASS logical=1 raw=256 f_out=256 simulator_creates=0 simulator_executes=0\n')
            check_host(log,'FPGA_REPLAY_V1',0)
            with self.assertRaises(RuntimeError):check_host(log,'IM2P_SIM',1)

    def test_decode_boundaries(self):
        data=reply()
        self.assertEqual(protocol.decode(data,17,0),(0,b''))
        for invalid in (data[:-1],data+b'x',data[:55]+bytes([data[55]^1])+data[56:],b'',reply(18),reply(17,1)):
            with self.assertRaises(ValueError): protocol.decode(invalid,17,0)
        bad=bytearray(data);bad[6]^=1
        with self.assertRaisesRegex(ValueError,'profile'):protocol.decode(bytes(bad),17)
        duplicate=bytearray(data[:-4]);struct.pack_into('<I',duplicate,20,4);duplicate.extend(b'\0'*4)
        duplicate.extend(struct.pack('<I',zlib.crc32(duplicate)))
        with self.assertRaisesRegex(ValueError,'completion payload'):protocol.decode(bytes(duplicate),17,0)

    def test_capacity(self):
        for shape in ((0,16,32),(33,16,32),(16,49,32),(16,16,33),(16,16,128)):
            with self.assertRaises(ValueError): protocol.packet(1,17,1,shape)
        with self.assertRaisesRegex(ValueError,'length'):protocol.packet(1,17,1,(16,16,32),b'x')

    def test_partial_io_and_sticky_failure(self):
        for corrupt in (False,True):
            master, slave=pty.openpty()
            transport=protocol.UART(os.ttyname(slave),timeout=2)
            request=protocol.packet(0,17,0)
            response=bytearray(reply());response[-1]^=int(corrupt)
            original_read,original_write=os.read,os.write
            errors=[]
            def peer():
                try:
                    captured=bytearray()
                    while len(captured)<len(request):captured.extend(original_read(master,128))
                    self.assertEqual(bytes(captured),request)
                    for value in response: original_write(master,bytes([value]))
                except BaseException as error:errors.append(error)
            thread=threading.Thread(target=peer,daemon=True);thread.start()
            try:
                with patch('os.write',side_effect=lambda fd,b:original_write(fd,b[:1])), patch('os.read',side_effect=lambda fd,n:original_read(fd,min(n,1))):
                    if corrupt:
                        with self.assertRaisesRegex(ValueError,'CRC'):transport.transfer(request)
                        with self.assertRaisesRegex(RuntimeError,'sticky'):transport.transfer(request)
                    else:self.assertEqual(transport.transfer(request),bytes(response))
                thread.join(2);self.assertFalse(thread.is_alive());self.assertEqual(errors,[])
            finally:transport.close();os.close(slave);os.close(master)


if __name__=='__main__':unittest.main()
