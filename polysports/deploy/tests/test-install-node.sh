#!/usr/bin/env bash
# 测试专用模拟下载；不连接官方服务器，也不修改系统目录。
set -Eeuo pipefail
root=$(cd "$(dirname "$0")/../../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/mock-bin" "$tmp/fixture/node-v24.99.0-linux-x64/bin"
cat > "$tmp/mock-bin/uname" <<'EOF'
#!/usr/bin/env bash
case "$1" in -s) echo Linux ;; -m) echo x86_64 ;; esac
EOF
cat > "$tmp/mock-bin/curl" <<'EOF'
#!/usr/bin/env bash
while [[ $# -gt 0 ]]; do
  case "$1" in -o) output=$2; shift 2 ;; https://*) url=$1; shift ;; *) shift ;; esac
done
cp "$MOCK_NODE_FIXTURE/${url##*/}" "$output"
EOF
cat > "$tmp/fixture/node-v24.99.0-linux-x64/bin/node" <<'EOF'
#!/usr/bin/env bash
echo v24.99.0
EOF
cat > "$tmp/fixture/node-v24.99.0-linux-x64/bin/npm" <<'EOF'
#!/usr/bin/env bash
echo mock-npm
EOF
chmod +x "$tmp/mock-bin/"* "$tmp/fixture/node-v24.99.0-linux-x64/bin/"*
export PATH="$tmp/mock-bin:$PATH" MOCK_NODE_FIXTURE="$tmp/fixture"
(cd "$tmp/fixture" && tar -cJf node-v24.99.0-linux-x64.tar.xz node-v24.99.0-linux-x64 && sha256sum -t node-v24.99.0-linux-x64.tar.xz > SHASUMS256.txt)
script="$root/polysports/deploy/scripts/install-node.sh"
bash "$script" "$tmp/installed"
bash "$script" "$tmp/installed"
mkdir "$tmp/conflict"
if bash "$script" "$tmp/conflict"; then echo '不应覆盖冲突目录' >&2; exit 1; fi
printf '损坏的模拟下载' >> "$tmp/fixture/node-v24.99.0-linux-x64.tar.xz"
if bash "$script" "$tmp/invalid"; then echo '不应接受校验失败的下载' >&2; exit 1; fi
[[ ! -e "$tmp/invalid" ]]
echo '模拟安装、重复运行、冲突及校验失败测试通过'
