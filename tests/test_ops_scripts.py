"""Серверные скрипты ops/*.sh: синтаксис и защита от повторения аварии с правами на .env.

Авария 2026-10-04: token-key-setup.sh выдал .env владельца по образцу своей же копии, созданной от root, —
.env стал root:root, пользователь booking перестал его читать, сервис упал и systemd перезапускал его по кругу."""
import os
import re
import shutil
import subprocess
import unittest

OPS = os.path.join(os.path.dirname(__file__), "..", "ops")
SCRIPTS = ["token-key-setup.sh", "payment-setup.sh", "ssh-harden.sh", "backup-setup.sh"]


def read(name):
    with open(os.path.join(OPS, name), encoding="utf-8") as f:
        return f.read()


def working_bash() -> bool:
    """На Windows в PATH бывает заглушка WSL `bash.exe`, которая не работает без установленного дистрибутива."""
    if not shutil.which("bash"):
        return False
    try:
        return subprocess.run(["bash", "-c", "true"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


class ScriptsTest(unittest.TestCase):
    @unittest.skipUnless(working_bash(), "рабочий bash не найден (на Windows это может быть заглушка WSL)")
    def test_every_script_is_valid_bash(self):
        for name in SCRIPTS:
            result = subprocess.run(["bash", "-n", os.path.join(OPS, name)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, f"{name}: {result.stderr}")

    def test_scripts_use_strict_mode(self):
        for name in SCRIPTS:
            self.assertIn("set -euo pipefail", read(name), name)

    def test_env_owner_is_never_copied_from_a_backup_made_by_root(self):
        """Владелец .env берётся от самого .env (до копирования), а не от его резервной копии."""
        for name in ("token-key-setup.sh", "payment-setup.sh"):
            self.assertNotRegex(read(name), r'chown --reference="\$ENV\.bak', name)
        key_script = read("token-key-setup.sh")
        self.assertIn("OWNER_SPEC=$(stat -c '%U:%G' \"$ENV\")", key_script)
        # владельца запоминают раньше, чем делают копию
        self.assertLess(key_script.index("OWNER_SPEC=$("), key_script.index('cp "$ENV" "$ENV.bak-key-$STAMP"'))
        self.assertIn('chown "$OWNER_SPEC" "$ENV" "$ENV.bak-key-$STAMP"', key_script)

    def test_env_and_its_backups_end_up_private(self):
        for name in ("token-key-setup.sh", "payment-setup.sh"):
            self.assertRegex(read(name), r"chmod 600", name)

    def test_key_script_is_idempotent_and_never_overwrites_an_existing_key(self):
        text = read("token-key-setup.sh")
        self.assertIn("TOKEN_ENCRYPTION_KEY=.\\+", text)
        self.assertIn("exit 0", text)

    def test_ssh_script_refuses_to_lock_you_out(self):
        text = read("ssh-harden.sh")
        self.assertIn("authorized_keys", text)
        self.assertIn("sshd -t", text)


if __name__ == "__main__":
    unittest.main()
