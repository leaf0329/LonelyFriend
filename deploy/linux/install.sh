#!/usr/bin/env bash
set -euo pipefail
umask 077

if [[ $EUID -ne 0 || $(uname -m) != x86_64 ]]; then
    echo 'Run as root on Ubuntu 24.04 amd64.' >&2
    exit 1
fi
if [[ $# -lt 3 || $# -gt 4 ]]; then
    echo 'Usage: install.sh PRIVATE_BUNDLE NAPCAT_SHELL_ZIP QQ_DEB [--enable-qzone-publish]' >&2
    exit 1
fi
bundle=$(realpath "$1")
napcat_zip=$(realpath "$2")
qq_deb=$(realpath "$3")
publish_option=${4:-}
if [[ -n $publish_option && $publish_option != --enable-qzone-publish ]]; then
    echo 'Unknown publication option.' >&2
    exit 1
fi
root=/opt/lonelyfriend
if [[ -e $root/data/cmd_config.json ]]; then
    echo 'An installation already exists. This installer does not overwrite live data.' >&2
    exit 1
fi
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends python3-venv git curl unzip xvfb xauth \
    libnss3 libgbm1 libgssapi-krb5-2 libglib2.0-0t64 libatk1.0-0t64 \
    libatspi2.0-0t64 libgtk-3-0t64 libasound2t64 ffmpeg fonts-noto-cjk
if ! id lonelyfriend >/dev/null 2>&1; then
    useradd --system --home-dir "$root" --shell /usr/sbin/nologin lonelyfriend
fi
install -d -m 0750 -o lonelyfriend -g lonelyfriend "$root"
tar --extract --gzip --file "$bundle" --directory "$root" --no-same-owner
cd "$root"
python3 -m venv .venv
.venv/bin/python -m pip install --disable-pip-version-check \
    --index-url https://mirrors.aliyun.com/pypi/simple -r requirements-linux.lock.txt
.venv/bin/python -m pip install --disable-pip-version-check \
    --index-url https://mirrors.aliyun.com/pypi/simple \
    -r data/plugins/astrbot_plugin_proactive_chat/requirements.txt
install -d runtime/qq runtime/napcat .cache logs
dpkg-deb --extract "$qq_deb" runtime/qq
unzip -q "$napcat_zip" -d runtime/napcat
.venv/bin/python - <<'PY'
import json
from pathlib import Path
root = Path('/opt/lonelyfriend')
package = root / 'runtime/qq/opt/QQ/resources/app/package.json'
value = json.loads(package.read_text())
if value['version'] != '3.2.34-53644':
    raise RuntimeError('The Linux QQ version does not match sources.lock.json')
value['main'] = './loadNapCat.js'
package.write_text(json.dumps(value, ensure_ascii=False) + '\n')
package.with_name('loadNapCat.js').write_text(
    "const { pathToFileURL } = require('node:url');\n"
    "import(pathToFileURL('/opt/lonelyfriend/runtime/napcat/napcat.mjs').href);\n"
)
PY
.venv/bin/python scripts/configure_server.py ${publish_option:+"$publish_option"}
chown -R lonelyfriend:lonelyfriend data runtime .venv .cache logs scripts plugins config tests deploy
chmod -R go-rwx data
install -m 0644 deploy/linux/lonelyfriend.service /etc/systemd/system/lonelyfriend.service
install -m 0644 deploy/linux/lonelyfriend-napcat.service /etc/systemd/system/lonelyfriend-napcat.service
systemd-analyze verify /etc/systemd/system/lonelyfriend.service /etc/systemd/system/lonelyfriend-napcat.service
systemctl daemon-reload
systemctl enable --now lonelyfriend.service lonelyfriend-napcat.service
