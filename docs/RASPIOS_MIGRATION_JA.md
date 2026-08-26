# Raspberry Pi OSへの移行とCodex引き継ぎ

この文書は、Ubuntuで使用していたカスタマイズ済みNanoVNASaverを
Raspberry Pi OSへ移行するための引き継ぎメモです。

## リポジトリ

- GitHub: `https://github.com/kishikawa7/nanovna-saver.git`
- 移行用ブランチ: `raspi-os-migration-20260825`
- 移行保存時のコミット: `8e48e9c9505687a84db7f0c7dcada878827b15de`

HTTPSで取得する場合:

```bash
git clone --branch raspi-os-migration-20260825 \
  https://github.com/kishikawa7/nanovna-saver.git
cd nanovna-saver
```

SSH鍵をGitHubへ登録済みの場合:

```bash
git clone --branch raspi-os-migration-20260825 \
  git@github.com:kishikawa7/nanovna-saver.git
cd nanovna-saver
```

## 移行先Codexへの引き継ぎプロンプト

Raspberry Pi OSでCodexを起動し、リポジトリのディレクトリを開いてから、
以下をそのまま貼り付けてください。

```text
このリポジトリは、UbuntuからRaspberry Pi OSへ移行したカスタマイズ済み
NanoVNASaverです。移行用ブランチ raspi-os-migration-20260825 の内容を
壊さずに、現在のRaspberry Piで実行できる状態までセットアップしてください。

次の順序で進めてください。

1. 最初に README.rst、docs/RASPIOS_MIGRATION_JA.md、pyproject.toml、
   uv.lock、contrib/99-nanovna-usb-reset.rules、git status、直近のコミットを
   読み、現在の変更と移行の前提を把握してください。
2. OSのバージョン、CPUアーキテクチャ（32/64 bit）、Pythonのバージョン、
   デスクトップ環境、接続予定のNanoVNA機種を確認してください。
3. 既存ファイルやユーザー設定を勝手に削除・上書きしないでください。
   必要なシステムパッケージのインストール、sudo、udevルールの配置など、
   管理者権限やシステム変更が必要な操作は、内容と理由を説明してから私に
   確認してください。
4. 可能ならuvとuv.lockを使って再現可能なPython環境を作ってください。
   Raspberry PiのCPUに対応するPySide6、NumPy、SciPyのwheelがない場合は、
   Raspberry Pi OSのaptパッケージ利用など、安全で保守しやすい代替案を
   調査して提案・実施してください。
5. NanoVNAのUSBシリアル認識、dialoutグループ、アクセス権を確認して
   ください。必要な場合は contrib/99-nanovna-usb-reset.rules を確認し、
   対象USB IDが実機と一致する場合に限ってudevへ導入してください。
6. Pythonのimport/コンパイル確認、テスト、アプリ起動確認を行ってください。
   実機への接続や測定開始は、ポートと機種を確認してから行ってください。
7. テストには既知の不一致があります。tests/test_sweep.pyは101点を期待
   しますが、src/NanoVNASaver/Settings/Sweep.pyの既定値は51点です。
   今回の移行障害と決めつけず、意図を確認して別件として報告してください。
8. セットアップ中に必要となったRaspberry Pi固有の手順は、この文書へ
   追記してください。コード変更が必要なら別ブランチで行い、変更理由、
   検証結果、残課題を最後に日本語でまとめてください。

まずは読み取り専用の調査を行い、検出した環境、予定する作業、確認が必要な
システム変更を簡潔に報告してから、可能な範囲のセットアップを続けてください。
```

## 移行時の注意

- NanoVNAを接続する前でも、依存関係の導入とGUI起動直前まで準備できます。
- Raspberry Pi OSの種類、CPU世代、32/64 bitによってPythonパッケージの
  導入方法が変わる可能性があります。
- udevルールはUSBリセット権限に関係します。実機のVendor ID/Product IDを
  `lsusb`で確認してから適用してください。
- 測定データ、校正データ、ユーザー設定など、Git管理外のデータはこの
  リポジトリには含まれません。Ubuntuを消去する前に別途バックアップして
  ください。

## Raspberry Pi OS 13 (trixie) / 64-bitでのセットアップ実績

2026-08-25に、aarch64のRaspberry Pi OS（Debian 13、Python 3.13.5、
Wayland/labwc）で次の手順を確認しました。システムPythonは既存の
移行用ブランチ取得時の`uv.lock`の`<3.13`条件と合わないため、uv管理の
Python 3.12を使用します。

```bash
curl -LsSf https://astral.sh/uv/install.sh | \
  env UV_UNMANAGED_INSTALL="$HOME/.local/bin" sh
"$HOME/.local/bin/uv" python install 3.12
"$HOME/.local/bin/uv" sync --frozen --python 3.12
```

この構成ではaarch64用PySide6、NumPy、SciPy wheelを利用でき、aptによる
ビルド依存の追加は不要でした。カスタム機能が使用するscikit-rfも通常依存に
含める必要があります。

USB接続前はudevルールを配置しません。接続後に`lsusb`とシリアルポートを
確認し、Vendor ID/Product IDが`0483:5740`と一致する場合だけ
`contrib/99-nanovna-usb-reset.rules`の導入を検討してください。また、実行
ユーザーが`dialout`グループに所属していない場合は管理者による追加と、
ログアウト・ログインが必要です。
