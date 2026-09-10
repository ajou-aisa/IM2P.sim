// The PTY carries real UART bits through the existing production shell/core.
// No expected values or numerical implementation are present in this process.
#define main itinerary_main
#include "../full_replay/rtl_driver.cpp"
#undef main
#include <fcntl.h>
#include <poll.h>
#include <unistd.h>
#include <cerrno>
#include <cstring>
#include <csignal>

static volatile sig_atomic_t stopped = 0;
int main(int argc, char **argv) {
    try {
        if (argc != 2) throw std::runtime_error("usage: rtl_pty inherited-master-fd");
        int fd = std::stoi(argv[1]);
        if (fcntl(fd, F_SETFL, O_NONBLOCK) < 0) throw std::runtime_error("PTY flags");
        signal(SIGTERM, [](int) { stopped = 1; });
        Driver driver;
        size_t emitted = 0, input_bytes = 0, output_bytes = 0;
        while (!stopped) {
            uint8_t bytes[256];
            ssize_t n = read(fd, bytes, sizeof(bytes));
            if (n > 0) {
                for (ssize_t i = 0; i < n; ++i) driver.byte(bytes[i]);
                input_bytes += n;
            } else if (n < 0 && errno != EAGAIN && errno != EIO && errno != EINTR) {
                throw std::runtime_error(std::strerror(errno));
            }
            // Autonomous device clock continues while the CPU prepares a stripe.
            driver.clocks(256);
            while (emitted < driver.received.size()) {
                ssize_t sent = write(fd, driver.received.data() + emitted, driver.received.size() - emitted);
                if (sent < 0 && (errno == EAGAIN || errno == EIO || errno == EINTR)) break;
                if (sent <= 0) throw std::runtime_error("PTY write");
                emitted += sent; output_bytes += sent;
            }
            if (emitted == driver.received.size()) { driver.received.clear(); emitted = 0; }
        }
        std::cout << "RTL_PTY_COMPLETE input_bytes=" << input_bytes << " output_bytes=" << output_bytes
                  << " launches=" << driver.launches << " ticks=" << driver.ticks << std::endl;
        return 0;
    } catch (const std::exception &e) {
        std::cerr << "RTL_PTY_FAIL " << e.what() << std::endl;
        return 1;
    }
}
