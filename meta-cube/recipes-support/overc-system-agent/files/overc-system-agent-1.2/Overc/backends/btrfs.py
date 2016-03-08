import sys, os
import tempfile
import subprocess
from Overc.utils  import Utils
from Overc.utils  import ROOTMOUNT

class Btrfs(Utils):
    def __init__(self):
        self.name = None
        self.rootfs = None
        self.rootdev = self._getrootdev()
        self.bakup_mode = self._get_bootmode()
        
        if os.path.exists('/boot/bzImage'):
            self.kernel = "/boot/bzImage"
        elif os.path.exists('/boot/uImage'):
            self.kernel = "/boot/uImage"

        self.kernel_md5 = self._compute_checksum(self.kernel)

        if not self.rootdev or not self.rootfs:
            print "Error: cannot get the rootfs device!"
            sys.exit(2)

    def _btrfs(self, args):
        return os.system('/usr/bin/btrfs %s' % args)

    def _getrootdev(self):
        subp=subprocess.Popen('mount',shell=True,stdout=subprocess.PIPE)
        c=subp.stdout.readline().strip()
        while c:
            c_list = c.split()
            if  c_list[2] == ROOTMOUNT:
                return c_list[0]
            c=subp.stdout.readline()

        return None
    
    def _mount_rootvolume(self):
        if not os.path.exists('/sysroot'):
            os.mkdir('/sysroot')

        subp=subprocess.Popen('mount',shell=True,stdout=subprocess.PIPE)
        c=subp.stdout.readline().strip()
        need_mount = True
        while c:
            c_list = c.split()
            if c_list[0] == self.rootdev and c_list[2] == "/sysroot":
                #root volume has been mount to /sysroot
                need_mount = False
                break
            c=subp.stdout.readline()
            
        if need_mount:
            os.system('mount -o subvolid=5 %s /sysroot' % self.rootdev)

    def _get_bootmode(self):
        self.rootfs = self._get_btrfs_value(ROOTMOUNT, "Name")
        if self.rootfs == "rootfs_bakup":
            return True
        else:
            return False

    def _get_btrfs_value(self, path, key):
        subp=subprocess.Popen('/usr/bin/btrfs subvolume show %s' % path, shell=True,stdout=subprocess.PIPE)
        c=subp.stdout.readline()
        while c:
            c_list = c.split(":", 1)
            if  c_list[0].strip() == key:
                return c_list[1].strip()
                
            c=subp.stdout.readline()

    def _cleanup_subvol(self):
        subp=subprocess.Popen('/usr/bin/btrfs subvolume list /sysroot',shell=True,stdout=subprocess.PIPE)
        c=subp.stdout.readline()
        subvol_stack = []
        while c:
            c_list = c.split()
            subvolid = c_list[8].split('/')[0]
            if  subvolid != self.rootfs:
                subvol = 'subvolume delete /sysroot/%s' % c_list[8]
                subvol_stack.append(subvol)
            c=subp.stdout.readline()

        while subvol_stack:
            self._btrfs(subvol_stack.pop())

    def _do_upgrade(self):
        #first remove the previous rootfs and bakup current running rootfs
        self._cleanup_subvol()
        upgrade_rootfs = {'rootfs':'rootfs_upgrade', 'rootfs_upgrade':'rootfs'}                   
        bzImage = {'normal':'bzImage_bakup', 'bakup':'bzImage'}

        if os.path.exists('/sysroot/%s' % upgrade_rootfs[self.rootfs]):
            self.message = 'Cannot cleanup the subvolumes in root tree /sysroot. There must be something wrong!'
            self.message += '\n'
            self.message += 'Upgrade aborted!'
            return False
            # sys.exit(2)

        if not self.bakup_mode:
            argv = 'subvolume snapshot /sysroot/%s /sysroot/rootfs_bakup' % self.rootfs
            self._btrfs(argv)

            #setup upgrade rootfs
            argv = 'subvolume snapshot /sysroot/%s /sysroot/%s' % (self.rootfs, upgrade_rootfs[self.rootfs])
            self._btrfs(argv)

            #do upgrade
            os.system('mount -t proc proc /sysroot/%s/proc' % upgrade_rootfs[self.rootfs])
            os.system('mount -o bind /dev /sysroot/%s/dev' % upgrade_rootfs[self.rootfs])
            os.system('mount --bind /run /sysroot/%s/run' % upgrade_rootfs[self.rootfs])

            tempd = tempfile.mkdtemp(dir='/sysroot/%s/tmp' % upgrade_rootfs[self.rootfs])
            os.system('cp -r /sysroot/%s/var/lib/rpm/* %s' % (upgrade_rootfs[self.rootfs], tempd))
            os.system('mount -t tmpfs tmpfs /sysroot/%s/var/lib/rpm' % upgrade_rootfs[self.rootfs])
            os.system('cp -r %s/* /sysroot/%s/var/lib/rpm/' % (tempd, upgrade_rootfs[self.rootfs]))

            result = os.system('chroot /sysroot/%s smart upgrade -y' % upgrade_rootfs[self.rootfs])

            os.system('cp -r /sysroot/%s/var/lib/rpm/* %s/' % (upgrade_rootfs[self.rootfs], tempd))
            os.system('umount /sysroot/%s/var/lib/rpm' % upgrade_rootfs[self.rootfs])
            os.system('cp -r %s/* /sysroot/%s/var/lib/rpm/' % (tempd, upgrade_rootfs[self.rootfs]))
            os.system('rm -rf %s' % tempd)

            os.system('umount /sysroot/%s/run' % upgrade_rootfs[self.rootfs])
            os.system('umount /sysroot/%s/dev' % upgrade_rootfs[self.rootfs])
            os.system('umount /sysroot/%s/proc' % upgrade_rootfs[self.rootfs])

            if result != 0:
                self.message = 'Error: System update failed! It has no effect on the running system!'
                self.message += '\n'
                self.message += 'Please do not run rollback!'
                return False
                # sys.exit(2)
            
            upgrade_bzImage = '/sysroot/%s/%s' % (upgrade_rootfs[self.rootfs], self.kernel)
            if os.path.islink(upgrade_bzImage):
                upgrade_kernel = '/sysroot/%s/%s' % (upgrade_rootfs[self.rootfs], os.path.realpath(upgrade_bzImage))
            else:
                upgrade_kernel = upgrade_bzImage

            upgrade_kernel_md5 = ''
            if os.path.exists(upgrade_kernel):
                upgrade_kernel_md5 = self._compute_checksum(upgrade_kernel)

            if upgrade_kernel_md5 and self.kernel_md5 != upgrade_kernel_md5:
                #backup kernel
                os.system('cp -f %s  %s_bakup' % (self.kernel, self.kernel))
                os.system('cp -f %s %s' % (upgrade_kernel, self.kernel))

            #setup default subvolume
            upgrade_subvolid = self._get_btrfs_value('/sysroot/%s' % upgrade_rootfs[self.rootfs], 'Object ID')
            argv = 'subvolume set-default %s /sysroot' % upgrade_subvolid
            self._btrfs(argv)
        else:
            self.message = "Error: You are running in the backup mode, cannot do upgrade!"
            return False
            # sys.exit(2)
        self.message = "do upgrade ok"
        return True
             
    def do_upgrade(self):
            self._mount_rootvolume()
            self._do_upgrade()

    def do_rollback(self):
        if self.bakup_mode:
            self.message = "Error: You are running in the backup mode, cannot do rollback!"
            return False
            # sys.exit(2)

        rollback_rootfs = {'rootfs':'rootfs_upgrade', 'rootfs_upgrade':'rootfs'}                   
        bzImage = {'normal':'bzImage_bakup', 'bakup':'bzImage'}

        self._mount_rootvolume()
        if not os.path.exists('/sysroot/%s' % rollback_rootfs[self.rootfs]):
            self.message = "Error: There is no previous status to rollback to!"
            return False
            # sys.exit(2)

        rollback_kernel = '/sysroot/%s/%s' % (rollback_rootfs[self.rootfs], self.kernel)
        rollback_kernel_md5 = ''
        if os.path.exists(rollback_kernel):
            if os.path.islink(rollback_kernel):
                rollback_kernel = '/sysroot/%s/%s' % (rollback_rootfs[self.rootfs], os.path.realpath(rollback_kernel))
        else:
            rollback_kernel = '%s_bakup' % self.kernel

        upgrade_kernel_md5 = self._compute_checksum(rollback_kernel)

        if rollback_kernel_md5 != self.kernel_md5:
            os.system('cp -f %s %s' % (rollback_kernel, self.kernel))

        #setup default subvolume
        rollback_subvolid = self._get_btrfs_value('/sysroot/%s' % rollback_rootfs[self.rootfs], 'Object ID')
        argv = 'subvolume set-default %s /sysroot' % rollback_subvolid
        self._btrfs(argv)

        self.message = "rollback succeeded"
        return True
        


