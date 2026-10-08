# Restore the pre-finalize working tree

Verified archive: `../backup-before-finalize-20261008-164539.zip` (85 MB; ZIP integrity check passed on 2026-10-08).

The archive contains the repository working tree as it existed before finalize work, including hidden and untracked files. It excludes `.git/`, `node_modules/`, and `.venv/` as required. The repository history remains in Git; the existing `backup/pre-finalize` tag is preserved, and `backup/pre-finalize-20261008` marks the current base commit.

To restore safely, extract into a new sibling directory. Do not extract over the active checkout:

```sh
mkdir ../restore-before-finalize
unzip ../backup-before-finalize-20261008-164539.zip -d ../restore-before-finalize
```

The extracted directory is a file-level restore and does not contain Git metadata or installed dependencies. Keep it isolated until you have checked its contents. The archive is outside the repository so it is not included in project commits.
