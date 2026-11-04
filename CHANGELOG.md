# Changelog

## [0.1.1](https://github.com/emindeniz99/apfs-compress/compare/v0.1.0...v0.1.1) (2026-11-04)


### Bug Fixes

* **tests:** use a made-up UUID in the quarantine value ([199200e](https://github.com/emindeniz99/apfs-compress/commit/199200ef977bc969aa5b62deb6f38623733f3736))
* **tool:** exit 4 on a failed file operation, and fail on an unread directory ([12614d8](https://github.com/emindeniz99/apfs-compress/commit/12614d8d0363b2bcd8a10d6d1d306a1bb8aa2c28))
* **tool:** finish an interrupted swap, rollback or finish on the next run ([876cc3c](https://github.com/emindeniz99/apfs-compress/commit/876cc3cea1c3bad5cce0a27bad7bccd16a8abd62))
* **tool:** let discard remove a copy left by a failed run after finish ([78612a7](https://github.com/emindeniz99/apfs-compress/commit/78612a78f68fe483591bab5be213389caa7301f6))
* **tool:** log the copy's own state on a DIFFERENT code-signature line ([0628775](https://github.com/emindeniz99/apfs-compress/commit/062877533b3dc76989e05ea5d5a38ed825100a45))
* **tool:** match the tree's path literally in the process check ([57b4550](https://github.com/emindeniz99/apfs-compress/commit/57b4550b8de5858c0b4921e51fad9d1542f9bb3c))
* **tool:** print uid and gid mismatches in decimal ([fcbfd6d](https://github.com/emindeniz99/apfs-compress/commit/fcbfd6d66d770b8aa83e910aeaabdeddcebd9dab))
* **tool:** refuse swap when the original changed since it was copied ([6852926](https://github.com/emindeniz99/apfs-compress/commit/6852926a9e1ebcb15d5e0ffaba774ebd5d83752a))
* **tool:** tell Java class files from fat Mach-O binaries ([59ed5a1](https://github.com/emindeniz99/apfs-compress/commit/59ed5a1c113b2e8d1e990f97adcc6afc6d23fb49))
