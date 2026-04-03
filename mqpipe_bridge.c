/*
 * mqpipe_bridge.c
 * Runs under Wine. Connects to \\.\pipe\mqpipe and proxies it to a
 * TCP socket on 127.0.0.1:29999 so native Linux code can connect.
 *
 * Build:
 *   x86_64-w64-mingw32-gcc mqpipe_bridge.c -o mqpipe_bridge.exe -lws2_32
 * Run:
 *   wine mqpipe_bridge.exe &
 */

#include <winsock2.h>
#include <windows.h>
#include <stdio.h>
#include <string.h>

#define PIPE_NAME   "\\\\.\\pipe\\mqpipe"
#define BRIDGE_PORT 29999
#define BUF_SIZE    65536

typedef struct {
    HANDLE pipe;
    SOCKET sock;
    volatile int dead;
} Bridge;

static DWORD WINAPI sock_to_pipe(LPVOID arg) {
    Bridge *b = (Bridge *)arg;
    char buf[BUF_SIZE];
    while (!b->dead) {
        int r = recv(b->sock, buf, BUF_SIZE, 0);
        if (r <= 0) { b->dead = 1; break; }
        int off = 0;
        while (off < r) {
            DWORD nw = 0;
            if (!WriteFile(b->pipe, buf + off, r - off, &nw, NULL)) {
                b->dead = 1; break;
            }
            off += nw;
        }
    }
    return 0;
}

static DWORD WINAPI pipe_to_sock(LPVOID arg) {
    Bridge *b = (Bridge *)arg;
    char buf[BUF_SIZE];
    while (!b->dead) {
        DWORD nr = 0;
        if (!ReadFile(b->pipe, buf, BUF_SIZE, &nr, NULL) || nr == 0) {
            b->dead = 1; break;
        }
        int off = 0;
        while (off < (int)nr) {
            int r = send(b->sock, buf + off, nr - off, 0);
            if (r <= 0) { b->dead = 1; break; }
            off += r;
        }
    }
    return 0;
}

static HANDLE open_pipe(void) {
    HANDLE h;
    for (;;) {
        h = CreateFile(PIPE_NAME, GENERIC_READ | GENERIC_WRITE,
                       0, NULL, OPEN_EXISTING, 0, NULL);
        if (h != INVALID_HANDLE_VALUE) break;
        fprintf(stderr, "[bridge] waiting for pipe...\n");
        Sleep(2000);
    }
    DWORD mode = PIPE_READMODE_MESSAGE;
    SetNamedPipeHandleState(h, &mode, NULL, NULL);
    fprintf(stderr, "[bridge] pipe connected\n");
    return h;
}

int main(void) {
    WSADATA wsa;
    WSAStartup(MAKEWORD(2, 2), &wsa);

    SOCKET srv = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    int yes = 1;
    setsockopt(srv, SOL_SOCKET, SO_REUSEADDR, (char *)&yes, sizeof(yes));

    struct sockaddr_in addr = {0};
    addr.sin_family      = AF_INET;
    addr.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    addr.sin_port        = htons(BRIDGE_PORT);

    if (bind(srv, (struct sockaddr *)&addr, sizeof(addr)) != 0) {
        fprintf(stderr, "[bridge] bind failed: %d\n", WSAGetLastError());
        return 1;
    }
    listen(srv, 1);
    fprintf(stderr, "[bridge] listening on 127.0.0.1:%d\n", BRIDGE_PORT);

    for (;;) {
        SOCKET client = accept(srv, NULL, NULL);
        if (client == INVALID_SOCKET) continue;
        fprintf(stderr, "[bridge] client connected\n");

        HANDLE pipe = open_pipe();
        Bridge b = { pipe, client, 0 };

        HANDLE t1 = CreateThread(NULL, 0, sock_to_pipe, &b, 0, NULL);
        HANDLE t2 = CreateThread(NULL, 0, pipe_to_sock, &b, 0, NULL);

        HANDLE threads[2] = { t1, t2 };
        WaitForMultipleObjects(2, threads, FALSE, INFINITE);
        b.dead = 1;
        WaitForMultipleObjects(2, threads, TRUE, 2000);

        CloseHandle(t1); CloseHandle(t2);
        CloseHandle(pipe);
        closesocket(client);
        fprintf(stderr, "[bridge] connection closed\n");
    }
}
