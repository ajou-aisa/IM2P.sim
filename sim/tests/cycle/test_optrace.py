#!/usr/bin/env python3
"""Strict production trace parsing and value-free replay regression tests."""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from sim.cycle import cli, optrace


def records(bits: int = 8, dim: int = 16) -> list[dict]:
    sources = {k:'1'*40 for k in ('IM2P.sim','llama.cpp-gemmini','headers')}
    digests = {k:'2'*64 for k in sources}
    run = dict(kind='run',sequence=0,run_id='test',schema='im2p-production-optrace',version=1,
               model='fixture',profile=f'a{bits}w{bits}-d{dim}-hp1',activation_bits=bits,
               weight_bits=bits,dim=dim,backend='IM2P_SIM/GEMMINI_HP1',mode='FULL',
               residual_enabled=True,prompt_tokens=256,requested_generated_tokens=5,
               source_commits=sources,source_worktree_sha256=digests)
    phase = dict(kind='phase',sequence=1,run_id='test',phase_id=0,phase_kind='prefill',
                 decode_index=None,input_tokens=256)
    work = dict(kind='npu_work',sequence=2,run_id='test',phase_id=0,layer='blk.0.ffn_down',
                operation='gemmini.matmul',provenance='dense_main',numerical_datapath='hp1_scu',
                scope='full',activation_bits=bits,weight_bits=bits,dim=dim,m=1,n=1,k=3072,
                tile_i_count=1,tile_j_count=1,tile_k_count=3,geometry_m=1,row_begin=0,row_count=1,
                stripe_id=None,host_slot=None,original_block_id=None,activation_stride_bytes=3072,
                weight_stride_bytes=1,output_stride_bytes=4,scale_stride_elements=1,block_size=32,
                vector_op=5,output_domain=2,production_geometry_version=1,work_context=0,
                source_row_begin=0,source_row_count=0,column_begin=0,group_index=0,logical_work_id=2,
                rmd_raw=False,host_integer_block_multiply=False)
    end = dict(kind='run_end',sequence=3,run_id='test',status='success',reason='',work_count=1,
               independent_counts=[dict(phase_id=0,layer=work['layer'],provenance='dense_main',count=1)])
    return [run,phase,work,end]


class TraceTests(unittest.TestCase):
    def parse(self, rows):
        return optrace.validate_records(rows)

    def test_all_six_profiles(self):
        for bits in (4,8):
            for dim in (16,32,64):
                self.assertEqual(len(self.parse(records(bits,dim)).works),1)

    def test_required_geometry_and_types(self):
        for field in ('tile_i_count','tile_j_count','tile_k_count','activation_stride_bytes','production_geometry_version'):
            r=records(); del r[2][field]
            with self.assertRaises(optrace.TraceError): self.parse(r)
        for value in (0,-1,True,'1',2**65):
            r=records(); r[2]['tile_k_count']=value
            with self.assertRaises(optrace.TraceError): self.parse(r)

    def test_schema_and_unknown_fields(self):
        for field,value in [('schema','other'),('version',2),('version',True)]:
            r=records(); r[0][field]=value
            with self.assertRaises(optrace.TraceError): self.parse(r)
        r=records();r[2]['weights']=[1,2]
        with self.assertRaises(optrace.TraceError): self.parse(r)

    def test_sequence_and_phase_integrity(self):
        r=records();r[2]['sequence']=1
        with self.assertRaises(optrace.TraceError):self.parse(r)
        r=records();r[2]['phase_id']=1
        with self.assertRaises(optrace.TraceError):self.parse(r)
        r=records();r[1]['phase_kind']='decode';r[1]['decode_index']=0
        with self.assertRaises(optrace.TraceError):self.parse(r)

    def test_residual_contract(self):
        r=records(); w=r[2];w.update(provenance='residual',scope='residual_compact',k=32,original_block_id=96)
        r[3]['independent_counts'][0]['provenance']='residual'
        self.parse(r)
        for field in ('rmd_raw','host_integer_block_multiply'):
            v=copy.deepcopy(r);v[2][field]=True
            with self.assertRaises(optrace.TraceError):self.parse(v)
        r[2]['original_block_id']=None
        with self.assertRaises(optrace.TraceError):self.parse(r)

    def test_stripe_range_and_profile(self):
        for change in ({'scope':'stripe','stripe_id':0,'host_slot':0,'row_begin':2},
                       {'dim':32},{'activation_bits':4},{'output_stride_bytes':3}):
            r=records();r[2].update(change)
            with self.assertRaises(optrace.TraceError):self.parse(r)

    def test_independent_count_required(self):
        for change in ([],[dict(phase_id=0,layer='other',provenance='dense_main',count=1)]):
            r=records();r[3]['independent_counts']=change
            with self.assertRaises(optrace.TraceError):self.parse(r)
        r=records();r[-1]['status']='failed'
        with self.assertRaises(optrace.TraceError):self.parse(r)
        with self.assertRaises(optrace.TraceError):self.parse(records()[:-1])

    def test_duplicate_json_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'bad.jsonl';p.write_text('{"kind":"run","kind":"phase"}\n')
            with self.assertRaises(optrace.TraceError):optrace.read_trace(p)

    def test_source_compatibility(self):
        r=records();t=self.parse(r)
        sources={k:r[0][k] for k in ('source_commits','source_worktree_sha256','profile')}
        optrace.check_sources(t,sources)
        sources=copy.deepcopy(sources);sources['source_commits']['IM2P.sim']='3'*40
        with self.assertRaises(optrace.TraceError):optrace.check_sources(t,sources)

    def test_expected_manifest_profile_compatibility(self) -> None:
        rows = records()
        trace = self.parse(rows)
        sources = {key:rows[0][key] for key in ('source_commits','source_worktree_sha256','profile')}
        optrace.check_sources(trace, sources)
        for profile in ('a4w4-d16-hp1', 'a8w8-d32-hp1', ''):
            sources['profile'] = profile
            with self.subTest(profile=profile), self.assertRaisesRegex(optrace.TraceError, 'incompatible profile'):
                optrace.check_sources(trace, sources)
        del sources['profile']
        with self.assertRaisesRegex(optrace.TraceError, 'incompatible profile'):
            optrace.check_sources(trace, sources)

    def test_no_observed_answer_in_model_input(self):
        r=records();r[2].update(observed_rtl_start=9000,observed_rtl_done=9001,observed_rtl_elapsed=1)
        t=self.parse(r)
        request=optrace.model_document(t,t.works[0])
        encoded=json.dumps(request)
        self.assertNotIn('observed',encoded);self.assertNotIn('9000',encoded)
        self.assertEqual(request['request']['tile_k'],3)
        self.assertEqual(request['request']['submission'],'planner-blocks')

    @unittest.skipUnless(os.environ.get('IM2P_CYCLE_LIBRARY'),'actual C library required')
    def test_real_vocabulary_work_exceeds_single_gemm_default_budget(self) -> None:
        r = records()
        r[2].update(m=1, n=50257, k=768, tile_i_count=1, tile_j_count=9,
                    tile_k_count=48, activation_stride_bytes=768,
                    weight_stride_bytes=50257, output_stride_bytes=201028,
                    scale_stride_elements=50257)
        trace = self.parse(r)
        sources = {key:r[0][key] for key in ('source_commits','source_worktree_sha256','profile')}
        result = optrace.replay(trace, Path(os.environ['IM2P_CYCLE_LIBRARY']), sources)
        self.assertEqual(result['work_count'], 1)
        self.assertGreater(result['isolated_cycle_sum'], 10_000_000)
        self.assertEqual(result['works'][0]['tile_counts'], [1,9,48])

    @unittest.skipUnless(os.environ.get('IM2P_CYCLE_LIBRARY'),'actual C library required')
    def test_actual_library_replay_deterministic(self):
        library=Path(os.environ['IM2P_CYCLE_LIBRARY'])
        for bits in (4,8):
            for dim in (16,32,64):
                r=records(bits,dim);t=self.parse(r)
                sources={k:r[0][k] for k in ('source_commits','source_worktree_sha256','profile')}
                a=optrace.replay(t,library,sources)
                b=optrace.replay(t,library,sources)
                default_budget = optrace.model_document(t, t.works[0])
                del default_budget['limits']
                original = cli.estimate(library, default_budget)
                self.assertEqual(a['works'][0]['model_result'], original['result'])
                self.assertEqual(a['works'][0]['submission_count'], original['result']['loop_count'])
                self.assertEqual(a,b);self.assertGreater(a['isolated_cycle_sum'],0)
                self.assertEqual(a['work_count'],1)
                self.assertEqual(a['works'][0]['tile_counts'],[1,1,3])

if __name__=='__main__':unittest.main()
