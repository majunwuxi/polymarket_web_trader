#!/usr/bin/env bash
set -Eeuo pipefail

target=${1:-/opt/polysports-node}
[[ "$target" = /* && "$target" != / ]] || { echo '请指定非根目录的绝对安装路径' >&2; exit 1; }
if [[ -e "$target" || -L "$target" ]]; then
  if [[ -x "$target/bin/node" && -x "$target/bin/npm" ]] && [[ $("$target/bin/node" --version) =~ ^v24\. ]]; then
    echo "已使用现有 Node.js 24：$target"
    echo "请在当前终端运行：export PATH=\"$target/bin:\$PATH\""
    exit 0
  fi
  echo "目标已存在但不是可用的 Node.js 24，未覆盖：$target" >&2
  exit 1
fi
case "$(uname -s):$(uname -m)" in
  Linux:x86_64) arch=linux-x64 ;;
  Linux:aarch64) arch=linux-arm64 ;;
  *) echo '仅支持 Linux x86_64 或 aarch64' >&2; exit 1 ;;
esac
tmp=$(mktemp -d)
trap 'rm -rf -- "$tmp"' EXIT
base=https://nodejs.org/dist/latest-v24.x
curl -fsSL "$base/SHASUMS256.txt" -o "$tmp/SHASUMS256.txt"
mapfile -t archives < <(awk -v arch="$arch" '$2 ~ ("^node-v24\\.[0-9]+\\.[0-9]+-" arch "\\.tar\\.xz$") {print $2}' "$tmp/SHASUMS256.txt")
[[ ${#archives[@]} -eq 1 ]] || { echo '官方校验清单不包含唯一的安装包' >&2; exit 1; }
archive=${archives[0]}
curl -fsSL "$base/$archive" -o "$tmp/$archive"
(cd "$tmp" && grep -F "  $archive" SHASUMS256.txt | sha256sum -c -)
mkdir "$tmp/extracted"
tar -xJf "$tmp/$archive" -C "$tmp/extracted"
source_dir="$tmp/extracted/${archive%.tar.xz}"
[[ -x "$source_dir/bin/node" && -x "$source_dir/bin/npm" ]] || { echo '安装包不完整' >&2; exit 1; }
[[ $("$source_dir/bin/node" --version) =~ ^v24\. ]] || { echo '安装包版本不正确' >&2; exit 1; }
mkdir "$target"
cp -a "$source_dir/." "$target/"
echo "Node.js 已安装：$target"
echo "请在当前终端运行：export PATH=\"$target/bin:\$PATH\""
