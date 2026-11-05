from __future__ import annotations

import argparse
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMPORT = 'import { selectHighwayServer } from "../../extensions/javascript/napcat-highway-server.mjs";\n'
BEFORE = '''  async upload(e, n, r, i, o) {
    const s = this.buildDataUpTrans(e, n, r, i, o);'''
AFTER = '''  async upload(e, n, r, i, o) {
    // Select before piping: a failed transfer can consume the upload stream.
    let selectedServer;
    try {
      selectedServer = await selectHighwayServer([
        { ip: this.server, port: this.port },
        ...this.sig.serverAddr,
        { ip: "htdata3.qq.com", port: 80 }
      ]);
    } catch (error) {
      n.destroy();
      throw error;
    }
    if (selectedServer.ip !== this.server || selectedServer.port !== this.port) {
      this.logger.warn(`[Highway] Selected reachable upload server ${selectedServer.ip}:${selectedServer.port}`);
      this.changeServer(selectedServer.ip, selectedServer.port);
    }
    const s = this.buildDataUpTrans(e, n, r, i, o);'''


def apply(root: Path, *, restore: bool = False) -> bool:
    path = root / 'runtime/napcat/napcat.mjs'
    source = path.read_text(encoding='utf-8')
    if restore:
        old, new = AFTER, BEFORE
        if source.count(IMPORT) != 1 or source.count(old) != 1:
            raise RuntimeError('The installed Highway patch cannot be restored')
        updated = source.replace(IMPORT, '', 1).replace(old, new, 1)
    else:
        if source.count(IMPORT) == 1 and source.count(AFTER) == 1:
            return False
        if IMPORT in source or source.count(BEFORE) != 1:
            raise RuntimeError('The Highway patch requires the pinned NapCat 4.18.41 client')
        if not (root / 'extensions/javascript/napcat-highway-server.mjs').is_file():
            raise RuntimeError('The Highway server selector is missing')
        updated = IMPORT + source.replace(BEFORE, AFTER, 1)
    stat = path.stat()
    descriptor, temporary = tempfile.mkstemp(prefix='.highway-', dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8', newline='') as stream:
            stream.write(updated)
        os.chmod(temporary, stat.st_mode)
        if os.name != 'nt':
            os.chown(temporary, stat.st_uid, stat.st_gid)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()
    return True


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Apply the pinned NapCat QQ upload server selection fix.')
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--restore', action='store_true')
    args = parser.parse_args()
    print({'changed': apply(args.root.resolve(), restore=args.restore)})
