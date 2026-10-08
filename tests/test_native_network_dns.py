import json
import pathlib
import platform
import shutil
import socket
import subprocess
import tempfile
import threading
import time
import unittest

from harness.web_fetch.network_dns import NativeDnsBroker, build_native_dns_library, validate_native_dns_library


class NativeDnsBrokerTests(unittest.TestCase):
    def query(self, broker, host, token=None):
        with socket.create_connection(broker.server.server_address, timeout=2) as connection:
            connection.sendall(f"{token or broker.token} {host}\n".encode())
            chunks = []
            while chunk := connection.recv(4096):
                chunks.append(chunk)
            return b"".join(chunks)

    def test_private_answers_remain_visible_and_invalid_auth_does_not_resolve(self):
        calls = []
        def resolver(host, timeout):
            calls.append((host, timeout))
            return [("10.0.0.1", 4), ("::1", 6)]
        with NativeDnsBroker(resolver=resolver) as broker:
            broker.begin_call(45)
            self.assertEqual(self.query(broker, "private.invalid", "wrong-token"), b"ERROR\n")
            self.assertEqual(calls, [])
            self.assertEqual(self.query(broker, "private.invalid"), b"OK\n4 10.0.0.1\n6 ::1\nEND\n")
            self.assertEqual(broker.traces[0]["addresses"], ["10.0.0.1", "::1"])
            self.assertLessEqual(calls[0][1], 45)

    def test_failure_or_expired_budget_never_falls_back(self):
        calls = []
        def resolver(host, timeout):
            calls.append(host)
            raise socket.gaierror("failed public resolver")
        with NativeDnsBroker(resolver=resolver) as broker:
            broker.begin_call(45)
            self.assertEqual(self.query(broker, "failed.invalid"), b"ERROR\n")
            broker.begin_call(0.001)
            time.sleep(0.002)
            self.assertEqual(self.query(broker, "expired.invalid"), b"ERROR\n")
            self.assertEqual(calls, ["failed.invalid"])

    def test_previous_call_cannot_contaminate_new_trace(self):
        entered, release = threading.Event(), threading.Event()
        def resolver(host, timeout):
            entered.set(); release.wait(2)
            return [("127.0.0.1", 4)]
        with NativeDnsBroker(resolver=resolver) as broker:
            broker.begin_call(45)
            answer = []
            thread = threading.Thread(target=lambda: answer.append(self.query(broker, "old.invalid")))
            thread.start(); self.assertTrue(entered.wait(2))
            broker.begin_call(45); release.set(); thread.join(2)
            self.assertEqual(answer, [b"ERROR\n"])
            self.assertEqual(broker.traces, [])


@unittest.skipUnless(platform.system() == "Darwin" and shutil.which("cc"), "Darwin native bridge")
class NativeDnsInteropTests(unittest.TestCase):
    def test_native_addresses_flags_concurrent_allocations_and_binding(self):
        source = r"""
#include <netdb.h>
#include <pthread.h>
#include <stdio.h>
#include <string.h>
static int failures;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static void *probe(void *unused) {
    (void)unused;
    for(int i=0;i<25;i++) {
        struct addrinfo hints={0}, *out=NULL;
        hints.ai_socktype=SOCK_STREAM; hints.ai_flags=AI_CANONNAME;
        int status=getaddrinfo("fixture.invalid","443",&hints,&out);
        int failed=status!=0;
        if(!status) {
            int count=0;
            for(struct addrinfo *row=out;row;row=row->ai_next) count++;
            failed=count!=2 || !out->ai_canonname || strcmp(out->ai_canonname,"fixture.invalid");
            freeaddrinfo(out);
        }
        if(failed) {pthread_mutex_lock(&lock);failures++;pthread_mutex_unlock(&lock);}
    }
    return NULL;
}
int main(void) {
    pthread_t threads[4];
    for(int i=0;i<4;i++) pthread_create(&threads[i],NULL,probe,NULL);
    for(int i=0;i<4;i++) pthread_join(threads[i],NULL);
    printf("%d\n",failures); return failures?1:0;
}
"""
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory)
            library, manifest = build_native_dns_library(path / "dns.dylib")
            probe = path / "probe.c"; probe.write_text(source)
            binary = path / "probe"
            subprocess.run(["cc", "-Wall", "-Wextra", "-Werror", str(probe), "-o", str(binary)], check=True)
            with NativeDnsBroker(resolver=lambda host,timeout: [("127.0.0.1",4),("::1",6)]) as broker:
                broker.begin_call(45)
                output = subprocess.run([str(binary)], env=broker.environment(library), capture_output=True, text=True, timeout=45)
                self.assertEqual(output.returncode, 0, output.stderr)
                self.assertEqual(output.stdout.strip(), "0")
                self.assertEqual(len(broker.traces), 100)
            library.write_bytes(library.read_bytes()+b"changed")
            with self.assertRaisesRegex(ValueError, "hash mismatch"):
                validate_native_dns_library(library)
