# ciafoward (`gba2cia`)

将 GBA 游戏 ROM (`.gba`) 一键打包为 Nintendo 3DS 原生硬件模式 (**`AGB_FIRM` Virtual Console**) 可安装的 `.cia` 格式命令行工具。

## 功能特性

- **原生 3DS 内置 GBA 硬件支持 (`AGB_FIRM` / `CoreVersion = 0x00000202`)**：
  - 使用 3DS 内置 ARM7TDMI 硬件运行 GBA 游戏（非软件模拟器，满速零延迟）。
  - 自动生成标准 **`0x360` 字节 `.CAA` 硬件配置尾部**（含存档芯片时序参数、LCD 残影控制与 256 级 RGB 色彩查找表）。
  - 默认内置 **1:1 原始亮丽色彩恢复 LUT**（去除官方大使版灰暗滤镜，可通过 `--dark-filter` 切换）以及 **零残影清晰显示**（`--ghosting 0`）。
  - 内置完整的 3DS Retail 证书链与签名模板，生成的 `.cia` 可直接通过 **FBI** 安装。
- **智能识别存档类型与实时时钟 (RTC)**：
  - 自动扫描 ROM 内置 Nintendo SDK 存档标识（`EEPROM_Vxxx`、`SRAM_Vxxx`、`FLASH_Vxxx`、`FLASH512_Vxxx`、`FLASH1M_Vxxx`）与 `SIIRTC_Vxxx` 时钟芯片标识。
  - 自动区分 `4kbit (512B)` 与 `64kbit (8KB)` EEPROM、`64KB Flash 512K (Panasonic/Macronix/Atmel)` 与 `128KB Flash 1M (Macronix/Sanyo)`，原生支持《高级战争》《宝可梦 宝石/火红/叶绿》等，无需手动打 SRAM 补丁。
- **自动下载配置游戏封面、标题画面与 3D Virtual Console 横幅**：
  - 根据 ROM 的 `CRC32`、4 字符游戏编号（如 `AWRE`）及文件名，自动匹配 **Libretro No-Intro 数据库**与内置经典游戏库，获取规范名称、发行商、发行年份与区域。
  - 自动从 Libretro Thumbnail CDN 下载 **游戏封面 (`Named_Boxarts`)** 与 **标题画面 (`Named_Titles`)** 并本地缓存。
  - 默认生成 **官方 3DS GBA Virtual Console 3D 掌机模型上屏预览 (`CBMD`)**（含游戏画面贴图、动态双行标题铭牌与经典 GBA 开机音效 `BCWAV`），以及无黑格失真的 PICA200 Morton Z-Order `SMDH` 桌面图标（`48x48` & `24x24`）。
- **模拟器存档转换与导入 (`ciafoward save`)**：
  - 支持将 mGBA (`.sav`)、RetroArch (`.srm`)、VBA (`.sav`) 存档一键转换为 3DS `AGB_FIRM` 精确硬件尺寸（自动剥离 RTC 尾部或 RetroArch 128KB 冗余填充，支持 EEPROM 8 字节字节序翻转），并附带完整的 **GodMode9** 注入指引。
- **纯 Python 跨平台零外部工具依赖**：
  - 无需外部 `makerom` 或 `bannertool` 二进制文件，纯 Python 即可在 macOS / Linux / Windows 上运行。

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

### 1. 一键打包 `.gba` 为 `.cia`（自动下载封面与游戏信息）

```bash
ciafoward "~/Downloads/Advance Wars (USA) (Rev 1).gba"
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
| `input` | 输入的 `.gba` ROM 文件路径 |
| `-o, --output` | 输出的 `.cia` 文件路径 |
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

