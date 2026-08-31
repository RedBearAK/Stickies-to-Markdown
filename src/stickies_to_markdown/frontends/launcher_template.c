/* @@MARKER@@
 * interpreter="@@INTERPRETER@@"
 * src_dir="@@SRC_DIR@@"
 * Regenerate with:  stickies2md --install-app
 *
 * Spawns the recorded interpreter with `-m stickies_to_markdown --menubar`
 * as a CHILD and waits for it. Not exec: after an exec the process image
 * would be python3.x and macOS would attribute folder-permission prompts
 * and grants to that binary. As a child of this signed Mach-O inside the
 * bundle, the bundle is the responsible process, so prompts carry the app
 * name and grants follow the app. stdout/stderr go to the launcher log.
 */
#include <fcntl.h>
#include <signal.h>
#include <spawn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <unistd.h>

extern char **environ;

static const char *INTERPRETER = "@@INTERPRETER@@";
static const char *SRC_DIR = "@@SRC_DIR@@";
static const char *LOG_RELATIVE = "@@LOG_RELATIVE@@";
static pid_t child = 0;

static void forward(int sig)
{
    if (child > 0)
        kill(child, sig);
}

static void mkdirs(char *path)
{
    for (char *p = path + 1; *p; p++) {
        if (*p == '/') {
            *p = 0;
            mkdir(path, 0755);
            *p = '/';
        }
    }
}

int main(int argc, char **argv)
{
    if (chdir("/") != 0) { /* ignore */ }

    const char *old = getenv("PYTHONPATH");
    size_t n = strlen(SRC_DIR) + (old ? strlen(old) + 1 : 0) + 1;
    char *pp = malloc(n);
    if (old && *old)
        snprintf(pp, n, "%s:%s", SRC_DIR, old);
    else
        snprintf(pp, n, "%s", SRC_DIR);
    setenv("PYTHONPATH", pp, 1);

    const char *home = getenv("HOME");
    char logpath[4096];
    snprintf(logpath, sizeof logpath, "%s/%s", home ? home : "/tmp", LOG_RELATIVE);
    mkdirs(logpath);

    posix_spawn_file_actions_t fa;
    posix_spawn_file_actions_init(&fa);
    posix_spawn_file_actions_addopen(&fa, 1, logpath, O_WRONLY | O_CREAT | O_APPEND, 0644);
    posix_spawn_file_actions_adddup2(&fa, 1, 2);

    char **args = calloc((size_t)argc + 5, sizeof(char *));
    int i = 0;
    args[i++] = (char *)INTERPRETER;
    args[i++] = "-m";
    args[i++] = "stickies_to_markdown";
    args[i++] = "--menubar";
    for (int a = 1; a < argc; a++)
        args[i++] = argv[a];
    args[i] = NULL;

    signal(SIGTERM, forward);
    signal(SIGINT, forward);
    signal(SIGHUP, forward);

    int rc = posix_spawn(&child, INTERPRETER, &fa, NULL, args, environ);
    if (rc != 0) {
        FILE *f = fopen(logpath, "a");
        if (f) {
            fprintf(f, "launcher: cannot start %s: %s\n", INTERPRETER, strerror(rc));
            fclose(f);
        }
        return 127;
    }

    int status = 0;
    while (waitpid(child, &status, 0) < 0) {
        /* interrupted by a forwarded signal; keep waiting */
    }
    return WIFEXITED(status) ? WEXITSTATUS(status) : 128;
}
