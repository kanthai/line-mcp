/*
 * binder_alloc — allocate Android binder devices on a binderfs mount.
 *
 * Usage: binder_alloc [/path/to/binderfs/binder-control]
 *        (default: /dev/binderfs/binder-control)
 *
 * Issues BINDER_CTL_ADD for "binder", "hwbinder" and "vndbinder". Run it from
 * the Proxmox HOST against the Redroid container's binderfs, reached through
 * /proc/<container-init-pid>/root/dev/binderfs/binder-control — see
 * redroid-binder-alloc. Android's own init cannot do this inside the container
 * (the ioctl is blocked by its seccomp filter), so without this helper zygote
 * and system_server never start and the container logs
 * "Binder driver '/dev/binder' could not be opened".
 *
 * An "EEXIST / File exists" result means the device was already allocated by an
 * earlier run; redroid-binder-alloc treats that as success.
 *
 * Build (on the host):  gcc -O2 -o binder_alloc binder_alloc.c
 */
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/sysmacros.h>
#include <unistd.h>

#include <linux/android/binderfs.h>

static const char *DEFAULT_CTL = "/dev/binderfs/binder-control";
static const char *NAMES[] = {"binder", "hwbinder", "vndbinder"};

static int alloc_binder(int ctl_fd, const char *name)
{
    struct binderfs_device dev;

    memset(&dev, 0, sizeof(dev));
    strncpy(dev.name, name, BINDERFS_MAX_NAME);
    dev.name[BINDERFS_MAX_NAME] = '\0';

    if (ioctl(ctl_fd, BINDER_CTL_ADD, &dev) < 0) {
        fprintf(stderr, "BINDER_CTL_ADD %s: %s\n", name, strerror(errno));
        return errno == EEXIST ? 0 : -1;
    }
    printf("Created /dev/binderfs/%s (major=%u, minor=%u)\n", name, dev.major, dev.minor);
    return 0;
}

int main(int argc, char **argv)
{
    const char *ctl = argc > 1 ? argv[1] : DEFAULT_CTL;
    int fd = open(ctl, O_RDONLY | O_CLOEXEC);
    int rc = 0;

    if (fd < 0) {
        perror("open binder-control");
        return 1;
    }
    for (size_t i = 0; i < sizeof(NAMES) / sizeof(NAMES[0]); i++) {
        if (alloc_binder(fd, NAMES[i]) < 0)
            rc = 1;
    }
    close(fd);
    return rc;
}
