"""Create a separate, trusted publisher checkout from a protected local mirror."""
from pathlib import Path
import secrets
from .bridge_executor import Executor
from .bridge_security import protected_dir, protected_tree


class Checkout:
    def __init__(self, mirror, root):
        self.mirror, self.root = Path(mirror), Path(root)
        protected_tree(self.mirror)
        protected_dir(self.root, 0o700)
        if self.mirror == self.root or self.mirror in self.root.parents or self.root in self.mirror.parents:
            raise ValueError('overlapping_checkout_root')

    def prepare(self, task):
        task.validate()
        dest = self.root / (task.task_id + '-' + secrets.token_hex(8))
        if Executor._git('--git-dir', str(self.mirror), 'rev-parse', 'refs/heads/main').strip().decode() != task.base_revision:
            raise ValueError('stale_base')
        # Reserve each new checkout privately before Git writes any contents.
        # The protected parent itself must already exist; never repair it here.
        dest.mkdir(mode=0o700)
        # The independent verifier expects Git's regular 0644 source mode even
        # when the supervisor uses 0077. Only these children receive 0022; both
        # containing directories stay 0700 and the supervisor mask is untouched.
        Executor._git('clone', '--no-local', '--no-checkout', '--', str(self.mirror), str(dest), umask=0o022)
        Executor._git('checkout', '--detach', task.base_revision, cwd=dest, umask=0o022)
        return dest
