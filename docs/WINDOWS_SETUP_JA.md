# Windowsでカスタマイズ版NanoVNASaverを使う

対象: Windows 10/11、Intel/AMDの64-bit PC。
リポジトリ: https://github.com/kishikawa7/nanovna-saver
ブランチ: `raspi-setup-20260825`。

## 方法1: exeをダウンロードする

1. [Windows Releaseの実行一覧](https://github.com/kishikawa7/nanovna-saver/actions/workflows/release_win.yml)を開く。
2. `raspi-setup-20260825`の最新コミットに対する成功済み実行（緑のチェック）を開く。
3. 下部のArtifactsから`NanoVNASaver.windows.x64`をダウンロードする。GitHubへのログインが必要。
4. ZIPを通常のフォルダーへ展開し、`nanovna-saver.exe`をダブルクリックする。

Pythonのインストールは不要。ビルド中・失敗の場合は方法2を使う。
このカスタマイズは本家NanoVNA-Saverの配布exeには含まれない。
Actionsの成果物には保存期限がある。期限切れの場合はWindows Releaseの
「Run workflow」で対象ブランチを選んで再ビルドするか、方法2を使う。

## 方法2: ソースから起動する

1. [Python公式ダウンロード](https://www.python.org/downloads/windows/)から
   **Python 3.12のWindows installer (64-bit)**を入れる。
   インストール時にPython launcher（`py`）を有効にする。
2. [対象ブランチ](https://github.com/kishikawa7/nanovna-saver/tree/raspi-setup-20260825)
   の「Code → Download ZIP」で取得して展開する。
3. 展開先の`setup-windows.bat`をダブルクリックする。
   初回はインターネット接続が必要。`uv.lock`の固定バージョンで依存関係を導入する。
4. `Setup complete`と表示されたら、`run-windows.bat`をダブルクリックする。
   次回以降は`run-windows.bat`だけでよい。

Gitを使う場合はPowerShellで次を実行してもよい。

```powershell
git clone --branch raspi-setup-20260825 https://github.com/kishikawa7/nanovna-saver.git
cd nanovna-saver
.\setup-windows.bat
.\run-windows.bat
```

PowerShellの実行ポリシーの変更は不要。Linux/Raspberry Piの`.venv`はコピーせず、
Windows上で作り直す。既に別OSの`.venv`をコピーしてしまった場合は、そのフォルダーを
別名へ退避してからセットアップする。

## NanoVNAを接続する

1. データ通信対応USBケーブルでNanoVNAを接続する。
2. Windowsのデバイスマネージャーの「ポート (COMとLPT)」で`COM3`などの番号を確認する。
3. アプリの「Serial port control」で「Rescan」、対象ポートを選び「Connect to device」。
   自動検出されない場合はポート欄に`COM3`などを手入力する。
4. 最初は通常のSweepで測定・保存できることを確認し、その後Batch sweepを使う。

ポートが出ない場合はケーブルを確認し、機種のメーカーが提供するUSBシリアル
ドライバーの案内に従う。同じCOMポートを他のアプリで開いている場合は閉じる。

## 既存の校正データを移す

測定・校正ファイルはGitHubには含まれないので、元のPC/Raspberry Piから別途コピーする。
Windows側の任意のフォルダーに置ける。起動時の自動探索は
`C:\Users\ユーザー名\Documents\Spara\caldata`以下で行う。
DocumentsがOneDriveなど別の場所にある場合は、次のボタンで実際の場所を指定する。

アプリの「External SOLT」で設定する。

- **Select ... / Folder**: NanoVNAで測定した校正フォルダー。
  必須: `S_short.s2p`, `S_open.s2p`, `S_load.s2p`, `S_thru.s2p`。
  `S_socket_short.s2p`, `S_socket_open.s2p`があれば、対応する補正にも使う。
- **Select reference ... / Standards**: 基準器の実測データフォルダー。
  必須: `S_short_P1.s2p`, `S_open_P1.s2p`, `S_load_P1.s2p`,
  `S_short_P2.s2p`, `S_open_P2.s2p`, `S_load_P2.s2p`, `S_thru.s2p`。

選択は保存され、次回起動時にも使われる。「Auto」は測定フォルダーだけを機種別の
既定場所に戻す。基準器フォルダーは変更しない。
理想校正（iCal）は測定フォルダーだけで使える。基準器の実測データがない場合は
通常の校正済み出力（`_cal`）が作られず、未校正出力と利用可能なiCal出力が保存される。

## Pico温度ログを使う場合

1. 既存の温度計用ファームウェアを入れたPicoをUSB接続する。
2. Batch sweep setupで「Save temperature log」にチェックし、「Pico port」で
   Picoの`COM4`などを選ぶ。ポートが一覧になければ手入力できる。
3. VNAとPicoには**別のCOMポート**を指定する。設定したポートは保存される。

Picoを使わない場合はチェックを外す。Picoの通信方式（`PING` / `READRAW`）は従来と同じ。

## 更新、exeの自作、起動トラブル

Gitで取得した場合は、アプリを閉じて`git pull --ff-only`後、
`setup-windows.bat`をもう一度実行する。ZIPの場合は新版を別のフォルダーに展開して
セットアップする。校正データや測定データは保持する。

自分のWindowsでexeを作る場合は、方法2のセットアップ後に`build-windows.bat`を実行する。
出力は`dist\nanovna-saver.exe`。

診断用の起動例（PowerShell）:

```powershell
.\run-windows.bat --debug --debug-file nanovna-debug.log
# exeの場合
.\nanovna-saver.exe --debug-file nanovna-debug.log
```

WindowsではLinuxのUSBレベルリセット（usbfs/udev）は使わない。
通常のシリアル再接続とデバイス再起動は共通処理を使う。
通信が復旧しない場合はUSBを抜き差しし、「Rescan」から接続し直す。

Windows ReleaseではWindows上のテスト、GUI起動、exeビルド、exe起動を確認する。
実機でのUSB接続・校正結果・長時間の連続測定は、接続先のWindows PCで確認する。
