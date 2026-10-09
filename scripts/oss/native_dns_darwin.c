/* Process-scoped DNS environment. Addresses still pass native product guards. */
#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <pthread.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>
#define MAX_ROOTS 256
#define MAX_REPLY 32768
struct allocation { struct addrinfo *head; struct addrinfo *tail; struct allocation *next; };
static struct allocation *allocations;
static pthread_mutex_t lock = PTHREAD_MUTEX_INITIALIZER;
static void release_allocation(struct allocation *item) {
    struct addrinfo *row = item->head;
    while (row) { struct addrinfo *next = row->ai_next; free(row->ai_addr); free(row->ai_canonname); free(row); row = next; }
    free(item);
}
static void dns_freeaddrinfo(struct addrinfo *head) {
    pthread_mutex_lock(&lock);
    struct allocation **cursor = &allocations;
    while (*cursor && (*cursor)->head != head) cursor = &(*cursor)->next;
    struct allocation *item = *cursor;
    if (item) *cursor = item->next;
    pthread_mutex_unlock(&lock);
    if (item) release_allocation(item); else freeaddrinfo(head);
}
static int dns_getaddrinfo(const char *node, const char *service, const struct addrinfo *hints, struct addrinfo **result) {
    if (!result) return EAI_FAIL;
    *result = NULL;
    if (!node || !*node || (hints && (hints->ai_flags & AI_NUMERICHOST))) return getaddrinfo(node, service, hints, result);
    struct addrinfo numeric = hints ? *hints : (struct addrinfo){0};
    numeric.ai_flags |= AI_NUMERICHOST;
    int status = getaddrinfo(node, service, &numeric, result);
    if (status == 0) return 0;
    const char *port = getenv("BROWSER_EVAL_DNS_PORT");
    const char *token = getenv("BROWSER_EVAL_DNS_TOKEN");
    if (!port || !token || strlen(token) != 64 || strlen(node) > 253 || strpbrk(node, "\r\n\t ")) return EAI_FAIL;
    char *end; long port_number = strtol(port, &end, 10);
    if (*end || port_number < 1 || port_number > 65535) return EAI_FAIL;
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) return EAI_FAIL;
    const char *budget = getenv("BROWSER_EVAL_DNS_TIMEOUT_SECONDS");
    char *budget_end; double seconds = budget ? strtod(budget, &budget_end) : 0;
    if (!budget || *budget_end || !(seconds > 0) || seconds > 3600) {close(fd); return EAI_FAIL;}
    struct timeval timeout = {.tv_sec = (long)seconds, .tv_usec = (long)((seconds - (long)seconds) * 1000000)};
    setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));
    setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &timeout, sizeof(timeout));
    int no_sigpipe = 1; setsockopt(fd, SOL_SOCKET, SO_NOSIGPIPE, &no_sigpipe, sizeof(no_sigpipe));
    struct sockaddr_in destination = {.sin_len = sizeof(destination), .sin_family = AF_INET, .sin_port = htons((uint16_t)port_number), .sin_addr = {.s_addr = htonl(INADDR_LOOPBACK)}};
    char request[512]; int length = snprintf(request, sizeof(request), "%s %s\n", token, node);
    if (connect(fd, (struct sockaddr *)&destination, sizeof(destination)) != 0) {close(fd); return EAI_FAIL;}
    int sent = 0;
    while (sent < length) { ssize_t n = send(fd, request + sent, (size_t)(length - sent), 0); if (n < 0 && errno == EINTR) continue; if (n <= 0) {close(fd); return EAI_FAIL;} sent += (int)n; }
    char reply[MAX_REPLY + 1]; size_t used = 0;
    for (;;) { ssize_t n = recv(fd, reply + used, MAX_REPLY - used, 0); if (n < 0 && errno == EINTR) continue; if (n < 0) {close(fd); return EAI_FAIL;} if (!n) break; used += (size_t)n; if (used == MAX_REPLY) {close(fd); return EAI_FAIL;} }
    close(fd); reply[used] = '\0';
    if (strncmp(reply, "OK\n", 3) != 0 || used < 7 || strcmp(reply + used - 4, "END\n") != 0) return EAI_FAIL;
    struct allocation *item = calloc(1, sizeof(*item));
    if (!item) return EAI_MEMORY;
    char *state; char *line = strtok_r(reply + 3, "\n", &state);
    int rows = 0;
    while (line && strcmp(line, "END") != 0) {
        int family; char address[INET6_ADDRSTRLEN]; char extra;
        if (++rows > MAX_ROOTS || sscanf(line, "%d %45s %c", &family, address, &extra) != 2 || (family != 4 && family != 6)) {release_allocation(item); return EAI_FAIL;}
        unsigned char bytes[16]; int af = family == 4 ? AF_INET : AF_INET6;
        if (inet_pton(af, address, bytes) != 1) {release_allocation(item); return EAI_FAIL;}
        if (!hints || hints->ai_family == AF_UNSPEC || hints->ai_family == af ||
            (hints->ai_family == AF_INET6 && af == AF_INET && (hints->ai_flags & AI_V4MAPPED))) {
            numeric.ai_family = hints && hints->ai_family != AF_UNSPEC ? hints->ai_family : af;
            struct addrinfo *root = NULL;
            status = getaddrinfo(address, service, &numeric, &root);
            if (status) {
                if ((numeric.ai_flags & AI_ADDRCONFIG) && status == EAI_NONAME) {
                    line = strtok_r(NULL, "\n", &state); continue;
                }
                release_allocation(item); return status;
            }
            for (struct addrinfo *native = root; native; native = native->ai_next) {
                struct addrinfo *copy = calloc(1, sizeof(*copy));
                if (!copy) {freeaddrinfo(root); release_allocation(item); return EAI_MEMORY;}
                *copy = *native; copy->ai_next = NULL; copy->ai_canonname = NULL;
                copy->ai_addr = malloc(native->ai_addrlen);
                if (!copy->ai_addr) {free(copy); freeaddrinfo(root); release_allocation(item); return EAI_MEMORY;}
                memcpy(copy->ai_addr, native->ai_addr, native->ai_addrlen);
                if (!item->head && hints && (hints->ai_flags & AI_CANONNAME)) {
                    copy->ai_canonname = strdup(node);
                    if (!copy->ai_canonname) {free(copy->ai_addr); free(copy); freeaddrinfo(root); release_allocation(item); return EAI_MEMORY;}
                }
                if (item->tail) item->tail->ai_next = copy; else item->head = copy;
                item->tail = copy;
            }
            freeaddrinfo(root);
        }
        line = strtok_r(NULL, "\n", &state);
    }
    if (!item->head) {release_allocation(item); return EAI_NONAME;}
    pthread_mutex_lock(&lock); item->next = allocations; allocations = item; pthread_mutex_unlock(&lock);
    *result = item->head; return 0;
}
__attribute__((used)) static struct { const void *replacement; const void *original; } interpose[]
__attribute__((section("__DATA,__interpose"))) = {
    {(const void *)(uintptr_t)&dns_getaddrinfo, (const void *)(uintptr_t)&getaddrinfo},
    {(const void *)(uintptr_t)&dns_freeaddrinfo, (const void *)(uintptr_t)&freeaddrinfo}
};
