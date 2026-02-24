# ini から VERSION を取得
$ini = Get-Content .\build.ini
$version = ($ini | Select-String -Pattern "VERSION").ToString().Split("=")[1].Trim()

# スクリプトファイルがある場所に移動する
Set-Location -Path $PSScriptRoot
# 各ファイルを置くフォルダを作成
New-Item -ItemType Directory -Force -Path ".\release_files\"
# ビルドフォルダを削除
Remove-Item -Path .\build -Recurse -Force

# 並列処理内で、処理が重いNerd Fontsのビルドを優先して処理する
$option_and_output_folder = @(
    @("--nerd-font", "NF-"), # ビルド 1:2幅 + Nerd Fonts
    @("--35 --nerd-font", "35NF-"), # ビルド 3:5幅 + Nerd Fonts
    @("--nerd-font --liga", "NFLG-"), # ビルド 1:2幅 + Nerd Fonts + リガチャ
    @("--35 --nerd-font --liga", "35NFLG-"), # ビルド 3:5幅 + Nerd Fonts + リガチャ
    @("", "-"), # ビルド 1:2幅
    @("--35", "35-"), # ビルド 3:5幅
    @("--liga", "LG-"), # ビルド 1:2幅 + リガチャ
    @("--35 --liga", "35LG-"), # ビルド 3:5幅 + リガチャ
    @("--jpdoc", "JPDOC-"), # ビルド 1:2幅 JPDOC版
    @("--35 --jpdoc", "35JPDOC-"), # ビルド 3:5幅 JPDOC版
    @("--hidden-zenkaku-space ", "HS-"), # ビルド 1:2 全角スペース不可視
    @("--hidden-zenkaku-space --35", "35HS-"), # ビルド 3:5 全角スペース不可視
    @("--hidden-zenkaku-space --liga", "HSLG-"), # ビルド 1:2 全角スペース不可視 + リガチャ
    @("--hidden-zenkaku-space --35 --liga", "35HSLG-"), # ビルド 3:5 全角スペース不可視 + リガチャ
    @("--hidden-zenkaku-space --jpdoc", "HSJPDOC-"), # ビルド 1:2 全角スペース不可視 JPDOC版
    @("--hidden-zenkaku-space --35 --jpdoc", "35HSJPDOC-"), # ビルド 3:5 全角スペース不可視 JPDOC版
    @("--right-four --nerd-font", "RFNF-"), # ビルド 1:2幅 + Nerd Fonts + 4のグリフ変更
    @("--right-four --35 --nerd-font", "35RFNF-"), # ビルド 3:5幅 + Nerd Fonts + 4のグリフ変更
    @("--right-four --nerd-font --liga", "RFNFLG-"), # ビルド 1:2幅 + Nerd Fonts + リガチャ + 4のグリフ変更
    @("--right-four --35 --nerd-font --liga", "35RFNFLG-"), # ビルド 3:5幅 + Nerd Fonts + リガチャ + 4のグリフ変更
    @("--right-four ", "RF-"), # ビルド 1:2幅 + 4のグリフ変更
    @("--right-four --35", "35RF-"), # ビルド 3:5幅 + 4のグリフ変更
    @("--right-four --liga", "RFLG-"), # ビルド 1:2幅 + リガチャ + 4のグリフ変更
    @("--right-four --35 --liga", "35RFLG-"), # ビルド 3:5幅 + リガチャ + 4のグリフ変更
    @("--right-four --jpdoc", "RFJPDOC-"), # ビルド 1:2幅 JPDOC版 + 4のグリフ変更
    @("--right-four --35 --jpdoc", "35RFJPDOC-"), # ビルド 3:5幅 JPDOC版 + 4のグリフ変更
    @("--hidden-zenkaku-space --right-four", "HSRF-"), # ビルド 1:2 全角スペース不可視 + 4のグリフ変更
    @("--hidden-zenkaku-space --right-four --35", "35HSRF-"), # ビルド 3:5 全角スペース不可視 + 4のグリフ変更
    @("--hidden-zenkaku-space --right-four --liga", "HSRFLG-"), # ビルド 1:2 全角スペース不可視 + リガチャ + 4のグリフ変更
    @("--hidden-zenkaku-space --right-four --35 --liga", "35HSRFLG-"), # ビルド 3:5 全角スペース不可視 + リガチャ + 4のグリフ変更
    @("--hidden-zenkaku-space --right-four --jpdoc", "HSRFJPDOC-"), # ビルド 1:2 全角スペース不可視 JPDOC版 + 4のグリフ変更
    @("--hidden-zenkaku-space --right-four --35 --jpdoc", "35HSRFJPDOC-") # ビルド 3:5 全角スペース不可視 JPDOC版 + 4のグリフ変更
)

$option_and_output_folder | Foreach-Object -ThrottleLimit 4 -Parallel {
    Write-Host "fontforge script start. option: `"$($_[0])`""
    Invoke-Expression "& `"C:\Program Files\FontForgeBuilds\bin\ffpython.exe`" .\fontforge_script.py --do-not-delete-build-dir $($_[0])" `
        && Write-Host "fonttools script start. option: `"$($_[1])`"" `
        && python3 fonttools_script.py $_[1]
}

$move_file_src_dest = @(
    @("UDEVGothic*HSRF*-*.ttf", "UDEVGothic_HSRF_$version"),
    @("UDEVGothic*RFNF*-*.ttf", "UDEVGothic_RFNF_$version"),
    @("UDEVGothic*RF*-*.ttf", "UDEVGothic_RF_$version"),
    @("UDEVGothic*HS*-*.ttf", "UDEVGothic_HS_$version"),
    @("UDEVGothic*NF*-*.ttf", "UDEVGothic_NF_$version"),
    @("UDEVGothic*-*.ttf", "UDEVGothic_$version")
)

$timestamp = Get-Date -Format "yyyyMMddHHmmss"
$move_dir = ".\release_files\build_$timestamp"

$move_file_src_dest | Foreach-Object {
    $folder_path = "$move_dir\$($_[1])"
    New-Item -ItemType Directory -Force -Path $folder_path
    Move-Item -Path ".\build\$($_[0])" -Destination $folder_path -Force
}
