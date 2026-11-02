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
        dest = self.root / (task.task_id + '-' + secrets.token_hex(8))
        if Executor._git('--git-dir', str(self.mirror), 'rev-parse', 'refs/heads/main').strip().decode() != task.base_revision:
            raise ValueError('stale_base')
        Executor._git('clone', '--no-local', '--no-checkout', '--', str(self.mirror), str(dest))
        Executor._git('checkout', '--detach', task.base_revision, cwd=dest)
        return dest
