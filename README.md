# ciafoward (`gba2cia`)

将经典掌机与主机游戏 ROM（**GBA / GB / GBC / NES / SNES / Sega Genesis / `.zip`**）一键打包为 Nintendo 3DS **Virtual Console / Forwarder** 可安装的 `.cia` 桌面图标命令行工具。

## 支持的主机平台与运行内核

| 平台 (`--system`) | 支持扩展名 | 3DS 运行内核 / 模拟架构 | 机型要求 |
|---|---|---|---|
| **GBA** (`gba`) | `.gba`, `.agb` | **3DS 内置 `AGB_FIRM` 裸机硬件模式** (`CoreVersion = 0x202`, `.CAA` 尾部) | 全系列 3DS / 2DS |
| **Game Boy** (`gb`) | `.gb`, `.sgb` | **任天堂官方 3DS GB Virtual Console**（支持下屏点阵绿屏与实机边框切换、存档点） | 全系列 3DS / 2DS |
| **Game Boy Color** (`gbc`) | `.gbc` | **任天堂官方 3DS GBC Virtual Console**（原生支持 MBC3 实时时钟 RTC 如《宝可梦 金/银/水晶》） | 全系列 3DS / 2DS |
| **NES / FC** (`nes`) | `.nes` | **任天堂官方 3DS NES Virtual Console**（自动将 iNES 转换为任天堂 `TNES` 映射器格式） | 全系列 3DS / 2DS |
| **SNES / SFC** (`snes`) | `.sfc`, `.smc` | **任天堂官方 New 3DS SNES Virtual Console**（自动剥离 `.smc` 头部并封装 `LoROM`/`HiROM` `.ves` `data.bin`） | New 3DS / New 2DS XL |
| **Sega Genesis / MD** (`md`) | `.md`, `.gen`, `.smd`, `.bin` | **PicoDrive 3DS 独立 Forwarder**（支持 `.smd` 奇偶交错自动解交错、60fps 满速运行与即时存档） | 全系列 3DS / 2DS |
| **ZIP 压缩包** | `.zip` | 自动解压并识别压缩包内的上述任意 ROM 格式 | 同对应平台 |

## 功能特性

- **原生 3DS 内置 GBA 硬件支持 (`AGB_FIRM` / `CoreVersion = 0x00000202`)**：
  - 使用 3DS 内置 ARM7TDMI 硬件运行 GBA 游戏（非软件模拟器，满速零延迟）。
  - 自动生成标准 **`0x360` 字节 `.CAA` 硬件配置尾部**（含存档芯片时序参数、LCD 残影控制与 256 级 RGB 色彩查找表）。
  - 默认内置 **1:1 原始亮丽色彩恢复 LUT**（去除官方大使版灰暗滤镜，可通过 `--dark-filter` 切换）以及 **零残影清晰显示**（`--ghosting 0`）。
  - 内置完整的 3DS Retail 证书链、多平台官方 VC 模板与纯 Python 3DS `IVFC` (RomFS) 三级 SHA-256 哈希树构建器，生成的 `.cia` 可直接通过 **FBI** 安装。
- **智能识别各平台头部与存档类型**：
  - **GBA**：自动扫描 Nintendo SDK 存档标识（`EEPROM_Vxxx`、`SRAM_Vxxx`、`FLASH_Vxxx`、`FLASH512_Vxxx`、`FLASH1M_Vxxx`）与 `SIIRTC_Vxxx` 时钟芯片标识。
  - **GB / GBC**：自动解析 `0x0100..0x014F` 头部、CGB 标志位、MBC1/MBC2/MBC3(RTC)/MBC5 卡带类型与 SRAM 大小。
  - **NES**：自动解析 iNES / NES 2.0 头部与 Mapper 编号，并自动转换为任天堂 3DS VC 专用的 16 字节 `TNES` 头部。
  - **SNES**：自动检测并剥离 512 字节 `.smc` 拷贝机头部，自动识别 `LoROM` (`0x7FC0`) 与 `HiROM` (`0xFFC0`) 并配置官方 VC Preset ID。
  - **Sega Genesis / MD**：自动检测并还原 `.smd` (Super Magic Drive) 16KB 奇偶交错块为标准线性 `.md` 镜像。
- **自动下载多平台封面、标题画面与 3D Virtual Console 横幅**：
  - 根据平台类型、ROM `CRC32`、游戏编号及文件名，自动查询对应平台的 **Libretro No-Intro 数据库**（GBA / GB / GBC / NES / SNES / Mega Drive），获取规范名称、发行商、发行年份与区域。
  - 自动从 Libretro Thumbnail CDN 下载对应平台的 **游戏封面 (`Named_Boxarts`)** 与 **标题画面 (`Named_Titles`)** 并本地缓存。
  - 默认生成 **3D Virtual Console 上屏预览 (`CBMD`)**（含游戏画面贴图、动态双行标题铭牌与各平台专属开机音效 `BCWAV`），以及无黑格失真的 PICA200 Morton Z-Order `SMDH` 桌面图标（`48x48` & `24x24`）。
- **模拟器存档转换与导入 (`ciafoward save`)**：
  - 支持将 mGBA (`.sav`)、RetroArch (`.srm`)、VBA (`.sav`) 存档一键转换为 3DS `AGB_FIRM` 精确硬件尺寸（自动剥离 RTC 尾部或 RetroArch 128KB 冗余填充，支持 EEPROM 8 字节字节序翻转），并附带完整的 **GodMode9** 注入指引。
- **纯 Python 跨平台零外部工具依赖**：
  - 无需外部 `makerom`、`bannertool` 或 `3dstool` 二进制文件，纯 Python 即可在 macOS / Linux / Windows 上运行。

---

## 安装方式

推荐使用 [`uv`](https://docs.astral.sh/uv/) 全局安装命令行工具（安装后可直接使用 `ciafoward` 或别名 `gba2cia`）：

```bash
# 从当前仓库目录安装
uv tool install .

# 或直接从 GitHub 仓库安装
uv tool install git+https://github.com/codescv/ciafoward.git
```

如果你在克隆的仓库目录中开发或直接运行，也可以使用 `uv run`：

```bash
uv run ciafoward --help
```

---

## 快速使用

### 1. 一键打包任意支持平台 ROM 为 `.cia`（自动识别平台、下载封面与游戏信息）

```bash
# GBA (原生 AGB_FIRM 硬件模式)
ciafoward "~/Downloads/Advance Wars (USA) (Rev 1).gba"

# Game Boy / Game Boy Color (官方 3DS GB/GBC Virtual Console)
ciafoward "~/Downloads/Tetris (World).gb"
ciafoward "~/Downloads/Pokemon - Crystal Version (USA, Europe).gbc"

# NES / FC (官方 3DS NES Virtual Console)
ciafoward "~/Downloads/Super Mario Bros. 3 (USA).nes"

# SNES / SFC (官方 New 3DS SNES Virtual Console)
ciafoward "~/Downloads/Super Mario World (USA).sfc"

# Sega Genesis / Mega Drive (PicoDrive 3DS Forwarder) 或 .zip 压缩包
ciafoward "~/Downloads/Sonic The Hedgehog 2 (World).md"
ciafoward "~/Downloads/Chrono Trigger (USA).zip"
```

指定输出路径，并导出横幅/图标 PNG 预览图：

```bash
ciafoward "~/Downloads/Advance Wars (USA) (Rev 1).gba" \
  -o "./output/Advance Wars (USA) (Rev 1).cia" \
  --preview-dir ./output/previews
```

### 2. 自定义封面、标题、发行商、补丁或存档类型

```bash
ciafoward build game.gba \
  -o game.cia \
  --title "Advance Wars" \
  --publisher "Nintendo" \
  --year "2001" \
  --cover ./my_cover.png \
  --title-screen ./my_title.png \
  --ips ./translation.ips \
  --save-type flash512 \
  --banner-style vc
```

### 3. 检查与校验 `.cia` 或 `.gba` 文件

校验 `.cia` 的证书链、TMD/NCCH/ExHeader/ExeFS/RomFS 全部 SHA-256 哈希、`.CAA` 硬件配置与内嵌 ROM 信息：

```bash
ciafoward inspect "./output/Advance Wars (USA) (Rev 1).cia"
ciafoward inspect "~/Downloads/Advance Wars (USA) (Rev 1).gba"
```

### 4. 转换并导入游戏存档 (`.sav` / `.srm` -> 3DS `AGB_FIRM`)

将模拟器存档（mGBA `.sav`、RetroArch `.srm`、VBA `.sav`）标准化为 3DS `AGB_FIRM` 精确硬件大小（自动裁剪 RetroArch 128KB 冗余填充或 mGBA 附加的 RTC 尾部，并支持 EEPROM 8 字节字节序翻转）：

```bash
# 普通 SRAM / Flash 存档标准化
ciafoward save "./output/Advance Wars (USA) (Rev 1).cia" ./my_save.srm -o ./AWRE.sav

# 若为 EEPROM 存档 (512B/8KB) 且来自老版本 VBA 模拟器，加上 --swap-eeprom 翻转字节序
ciafoward save game.gba vba_eeprom.sav -o 3ds_eeprom.sav --swap-eeprom
```

**在 3DS 上通过 GodMode9 导入存档步骤：**
1. 将转换后的 `.sav` 文件复制到 3DS SD 卡（例如 `/gm9/out/AWRE.sav`）。
2. 在 3DS 桌面先启动一次该 GBA 游戏（使系统在 NAND `agbsave` 分区注册该游戏 TitleID），随后按 `HOME` 键退出。
3. 关机后按住 **`Start`** 键开机进入 **GodMode9**：
   - 进入 `[0:] SDCARD` 找到 `AWRE.sav`，按 **`Y`** 键复制（Copy）。
   - 返回主菜单进入 `[S:] SYSNAND VIRTUAL` -> 选择 `agbsave.bin` -> 按 **`A`**。
   - 选择 `AGBSAVE options...` -> **`Inject GBA VC save`** -> 按 **`A`** 确认，按 `Start` 重启即可！

---

## 命令行参数一览

| 参数 | 说明 |
|---|---|
| `input` | 输入的游戏 ROM 文件路径 (`.gba`, `.gb`, `.gbc`, `.nes`, `.sfc`, `.smc`, `.md`, `.gen`, `.smd`, `.zip`) |
| `-o, --output` | 输出的 `.cia` 文件路径 |
| `--system` | 指定主机平台类型：`auto`（默认自动识别）、`gba`、`gb`、`gbc`、`nes`、`snes`、`md` |
| `--title` | 自定义游戏显示名称（同时作用于桌面图标与 3D 横幅铭牌） |
| `--publisher` | 自定义发行商名称（例如 `Nintendo`） |
| `--year` | 自定义发行年份（例如 `2001`） |
| `--cover` | 自定义封面图片路径 (`PNG`/`JPG`) |
| `--title-screen` | 自定义游戏标题画面路径 (`PNG`/`JPG`，用于 3D GBA 模型屏幕贴图) |
| `--banner-style` | `vc`（默认，3D GBA Virtual Console 模型）、`2d`（2D 封面+铭牌卡片）或 `full`（2D 全屏封面） |
| `--save-type` | `auto`（默认）、`sram`、`flash512`、`flash1m`、`eeprom4k`、`eeprom64k`、`none` |
| `--rtc` / `--no-rtc` | 强制开启或关闭实时时钟 (Seiko S-3511A RTC) |
| `--title-id` | 自定义 16 位 UniqueID（如 `0x81234`）或完整 64 位 TitleID |
| `--ghosting` | LCD 帧间残影强度 `0..255`（默认 `0` 清晰无残影） |
| `--dark-filter` | 启用官方大使版暗色滤镜（默认关闭，保持 1:1 原始亮丽色彩） |
| `--ips` | 打包前自动应用的 `.ips` 补丁路径 |
| `--preview-dir` | 导出生成的 `256x128` 横幅与 `48x48` 图标 PNG 预览图目录 |
| `--offline` | 离线模式（不联网下载封面，使用内置数据库与自动渲染封面） |
| `--json` | 以 JSON 格式输出打包或检查结果 |

---

## 开发与测试

```bash
uv run python -m unittest discover -s tests -v
```

