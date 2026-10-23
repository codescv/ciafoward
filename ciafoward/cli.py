"""
Command-Line Interface for `ciafoward` (GBA to 3DS AGB_FIRM Virtual Console CIA Packager).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional

from . import __version__
from .cia import (
    extract_base_templates_from_cia,
    package_gba_to_cia,
    verify_and_inspect_cia,
)
from .consoles import parse_rom_auto
from .gba import GBARomInfo, apply_ips_patch, parse_gba_rom
from .metadata import resolve_game_metadata
from .save_tool import convert_save_for_3ds

SUBCOMMANDS = {"build", "inspect", "info", "extract-base", "save"}


def _format_size(num_bytes: int) -> str:
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.2f} MB ({num_bytes:,} B)"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.2f} KB ({num_bytes:,} B)"
    return f"{num_bytes} B"


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ciafoward",
        description=(
            "将多平台经典游戏 (GBA / GB / GBC / NES / SNES / Sega Genesis / .zip) 打包为 "
            "Nintendo 3DS Virtual Console / Forwarder 可安装的 .cia 格式，支持自动识别存档类型、自动下载封面与游戏元数据。"
        ),
    )
    parser.add_argument(
        "-V", "--version", action="version", version=f"ciafoward {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # 1. `build` subcommand (also default when given a ROM file directly)
    build_p = subparsers.add_parser(
        "build",
        help="将游戏 ROM (.gba/.gb/.gbc/.nes/.sfc/.smc/.md/.gen/.zip) 打包为 3DS .cia 安装包 (默认命令)",
    )
    build_p.add_argument(
        "input",
        help="输入的游戏 ROM 文件路径 (.gba, .gb, .gbc, .nes, .sfc, .smc, .md, .gen, .smd, .zip)",
    )
    build_p.add_argument(
        "-o",
        "--output",
        help="输出的 CIA 文件路径 (默认输出到当前目录下的 <游戏名>.cia)",
    )
    build_p.add_argument(
        "--system",
        default="auto",
        choices=["auto", "gba", "gb", "gbc", "nes", "snes", "md"],
        help="指定主机平台类型 (默认: auto 自动根据扩展名与 ROM 魔数识别)",
    )
    build_p.add_argument("--title", help="自定义游戏显示名称 (Short & Long Title)")
    build_p.add_argument("--publisher", help="自定义发行商名称 (例如 Nintendo)")
    build_p.add_argument("--year", help="自定义发行年份 (例如 2001)")
    build_p.add_argument("--cover", help="自定义封面图片路径 (PNG/JPG)")
    build_p.add_argument("--title-screen", help="自定义标题画面图片路径 (PNG/JPG)")
    build_p.add_argument(
        "--banner-style",
        choices=["vc", "2d", "full"],
        default="vc",
        help="3DS 上屏横幅样式: 'vc' (默认 3D GBA Virtual Console 模型)、'2d' (2D 封面+铭牌卡片) 或 'full' (2D 全屏封面)",
    )
    build_p.add_argument(
        "--save-type",
        default="auto",
        choices=[
            "auto",
            "sram",
            "flash512",
            "flash1m",
            "eeprom4k",
            "eeprom64k",
            "none",
        ],
        help="AGB_FIRM 硬件存档类型 (默认: auto 自动从 ROM 识别)",
    )
    rtc_group = build_p.add_mutually_exclusive_group()
    rtc_group.add_argument(
        "--rtc",
        dest="rtc",
        action="store_true",
        default=None,
        help="强制启用实时时钟 (Seiko S-3511A RTC)",
    )
    rtc_group.add_argument(
        "--no-rtc",
        dest="rtc",
        action="store_false",
        help="强制禁用实时时钟 (RTC)",
    )
    build_p.add_argument(
        "--title-id",
        help="自定义 16 位 UniqueID (例如 0x81234) 或完整 64 位 TitleID",
    )
    build_p.add_argument(
        "--ghosting",
        type=int,
        default=0,
        help="LCD 帧间残影强度 0..255 (默认 0 = 清晰无残影)",
    )
    build_p.add_argument(
        "--dark-filter",
        action="store_true",
        help="启用官方大使版暗色滤镜 (默认关闭，使用 1:1 原始亮丽色彩 LUT)",
    )
    build_p.add_argument("--ips", help="打包前自动应用的 .ips 补丁文件路径")
    build_p.add_argument(
        "--base-code",
        help="自定义 AGB_FIRM ARM11 .code stub 模板文件路径",
    )
    build_p.add_argument(
        "--base-cia",
        help="从现有的 GBA VC .cia 提取底层模板后再打包",
    )
    build_p.add_argument(
        "--preview-dir",
        help="将生成的 256x128 3DS 横幅和 48x48 图标导出为 PNG 预览图的目录",
    )
    build_p.add_argument(
        "--offline",
        action="store_true",
        help="离线模式：不联网查询 Libretro 数据库或下载封面，使用内置数据库与自动生成封面",
    )
    build_p.add_argument(
        "--json",
        action="store_true",
        help="以 JSON 格式输出打包结果摘要",
    )

    # 2. `inspect` / `info` subcommand
    for cmd_name in ("inspect", "info"):
        insp_p = subparsers.add_parser(
            cmd_name,
            help="查看并校验 .gba ROM 或生成的 .cia 安装包结构、哈希与元数据",
        )
        insp_p.add_argument("target", help="要检查的 .gba 或 .cia 文件路径")
        insp_p.add_argument("--json", action="store_true", help="输出 JSON 格式")

    # 3. `extract-base` subcommand
    ext_p = subparsers.add_parser(
        "extract-base",
        help="从现有的 GBA Virtual Console .cia 提取并缓存 ARM11 code/exheader/banner 模板",
    )
    ext_p.add_argument("cia_file", help="现有的 GBA VC .cia 文件路径")

    # 4. `save` subcommand
    save_p = subparsers.add_parser(
        "save",
        help="转换并准备模拟器存档 (.sav / .srm) 以导入 3DS GBA Virtual Console (输出精确硬件尺寸与 GodMode9 导入指引)",
    )
    save_p.add_argument("target", help="对应的 .gba ROM 或生成的 .cia 文件路径")
    save_p.add_argument("input_save", help="输入的模拟器存档文件路径 (.sav 或 .srm)")
    save_p.add_argument(
        "-o",
        "--output",
        help="输出的 3DS 专用 .sav 文件路径 (默认输出为 <存档名>_3ds_<游戏编号>.sav)",
    )
    save_p.add_argument(
        "--swap-eeprom",
        action="store_true",
        help="对 EEPROM 存档 (512B/8KB) 进行 8 字节 (64-bit) 字节序反转 (用于 VBA 存档与 3DS 互转)",
    )
    save_p.add_argument("--json", action="store_true", help="输出 JSON 格式")

    return parser


def _parse_unique_id(val: Optional[str]) -> Optional[int]:
    if not val:
        return None
    v = int(val.strip(), 16 if val.strip().lower().startswith("0x") else 16)
    if v > 0xFFFFF:
        # Extract UniqueID from 0x000400000XXXXX00
        return (v >> 8) & 0xFFFFF
    return v & 0xFFFFF


def cmd_build(args: argparse.Namespace) -> int:
    input_path = Path(args.input).expanduser().resolve()
    if not input_path.is_file():
        print(f"[错误] 找不到输入的 ROM 文件: {input_path}", file=sys.stderr)
        return 1

    if args.base_cia:
        saved = extract_base_templates_from_cia(args.base_cia)
        if not args.json:
            print(f"[*] 已从基准 CIA 提取模板: {saved}")

    raw_input_bytes = input_path.read_bytes()
    force_sys = None if getattr(args, "system", "auto") == "auto" else args.system
    custom_uid = _parse_unique_id(args.title_id)

    rom_info, rom_bytes = parse_rom_auto(
        file_path=str(input_path),
        rom_bytes=raw_input_bytes,
        override_save_type=args.save_type,
        override_rtc=args.rtc,
        custom_unique_id=custom_uid,
        force_system=force_sys,
    )

    if args.ips:
        ips_path = Path(args.ips).expanduser().resolve()
        if not ips_path.is_file():
            print(f"[错误] 找不到 IPS 补丁文件: {ips_path}", file=sys.stderr)
            return 1
        rom_bytes = apply_ips_patch(rom_bytes, ips_path.read_bytes())
        rom_info, rom_bytes = parse_rom_auto(
            file_path=rom_info.file_path,
            rom_bytes=rom_bytes,
            override_save_type=args.save_type,
            override_rtc=args.rtc,
            custom_unique_id=custom_uid,
            force_system=rom_info.system,
        )
        if not args.json:
            print(f"[*] 已应用 IPS 补丁: {ips_path.name}")

    if not args.json:
        print("=" * 68)
        print(f"  {rom_info.system_name} -> 3DS Virtual Console CIA 打包工具 v{__version__}")
        print("=" * 68)
        print(f"[1/4] 解析 {rom_info.system_name} ({rom_info.system.upper()}) ROM 信息:")
        print(f"      文件名称     : {Path(rom_info.file_path).name}")
        print(f"      原始大小     : {_format_size(rom_info.raw_size)} -> 打包载荷 {_format_size(rom_info.padded_size)}")
        print(f"      内部标题     : {rom_info.internal_title}")
        print(f"      游戏编号     : {rom_info.game_code} ({rom_info.product_code}) | 区域: {rom_info.region}")
        print(f"      厂商代码     : {rom_info.maker_code} | 修订版本: Rev {rom_info.version}")
        print(f"      CRC32 校验   : {rom_info.crc32_hex} | 头部校验: {'通过' if rom_info.header_valid else '非标准'}")
        print(
            f"      硬件/存档特征: {', '.join(rom_info.sdk_save_strings) if rom_info.sdk_save_strings else '无'}"
            f" | RTC 时钟: {'是' if rom_info.has_rtc else '否'}"
        )
        print(f"      3DS 运行模式 : [0x{rom_info.save_type_id:02X}] {rom_info.save_type_name}")
        print(f"      3DS TitleID  : {rom_info.title_id_hex}")

    if not args.json:
        print(
            f"\n[2/4] {'(离线模式) 解析游戏信息与生成封面...' if args.offline else '自动查询游戏元数据并下载封面与标题画面...'}"
        )

    metadata = resolve_game_metadata(
        rom_info=rom_info,
        custom_title=args.title,
        custom_publisher=args.publisher,
        custom_year=args.year,
        custom_cover=args.cover,
        custom_title_screen=args.title_screen,
        download_artwork=not args.offline,
        use_online_db=not args.offline,
    )

    if not args.json:
        print(f"      游戏名称     : {metadata.short_title}")
        print(f"      发行厂商     : {metadata.publisher} ({metadata.release_year})")
        print(f"      No-Intro 匹配: {metadata.canonical_name} [来源: {metadata.source}]")
        if metadata.boxart_path:
            print(f"      游戏封面图   : 已就绪 ({metadata.boxart_path})")
            if metadata.boxart_url:
                print(f"                     来源: {metadata.boxart_url}")
        else:
            print("      游戏封面图   : 未找到在线封面，将自动渲染 GBA Virtual Console 定制封面")
        if metadata.title_screen_path:
            print(f"      标题画面图   : 已就绪 ({metadata.title_screen_path})")

    # Determine output CIA path
    if args.output:
        output_cia_path = str(Path(args.output).expanduser().resolve())
    else:
        safe_filename = "".join(
            c if c not in '<>:"/\\|?*' else "_" for c in metadata.canonical_name
        ).strip() or input_path.stem
        output_cia_path = str(Path.cwd() / f"{safe_filename} [{rom_info.game_code}].cia")

    if not args.json:
        print(f"\n[3/4] 构建 3DS SMDH 图标、256x128 CBMD 横幅、GBA 启动音效与 AGB_FIRM (.CAA) 镜像...")

    result = package_gba_to_cia(
        rom_bytes=rom_bytes,
        rom_info=rom_info,
        metadata=metadata,
        output_cia_path=output_cia_path,
        ghosting=max(0, min(255, args.ghosting)),
        dark_filter=args.dark_filter,
        banner_style=args.banner_style,
        custom_code_path=args.base_code,
        download_templates=not args.offline,
        save_preview_dir=args.preview_dir,
    )

    verification = verify_and_inspect_cia(result.output_cia_path)

    if args.json:
        print(
            json.dumps(
                {
                    "output_cia_path": result.output_cia_path,
                    "cia_size": result.cia_size,
                    "cia_sha256": result.cia_sha256,
                    "system": rom_info.system,
                    "system_name": rom_info.system_name,
                    "title_id": rom_info.title_id_hex,
                    "product_code": rom_info.product_code,
                    "game_code": rom_info.game_code,
                    "crc32": rom_info.crc32_hex,
                    "save_type_id": rom_info.save_type_id,
                    "save_type_name": rom_info.save_type_name,
                    "short_title": metadata.short_title,
                    "publisher": metadata.publisher,
                    "release_year": metadata.release_year,
                    "boxart_path": metadata.boxart_path,
                    "title_screen_path": metadata.title_screen_path,
                    "preview_banner_png": result.preview_banner_png_path,
                    "preview_icon_png": result.preview_icon_png_path,
                    "verification": {
                        "all_hashes_valid": verification["all_hashes_valid"],
                        "romfs_ivfc_valid": verification.get("romfs_ivfc_valid", True),
                        "agb_firm_ready": verification["agb_firm_ready"],
                    },
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    print(f"[4/4] 校验并封装 CIA 安装包完成:")
    print(f"      输出文件     : {result.output_cia_path}")
    print(f"      CIA 大小     : {_format_size(result.cia_size)}")
    print(f"      CIA SHA-256  : {result.cia_sha256}")
    mode_label = (
        f"AGB_FIRM 硬件模式 (CoreVersion={verification['core_version']})"
        if rom_info.system == "gba"
        else f"{rom_info.system_name} VC 模式 (CoreVersion={verification['core_version']})"
    )
    print(
        f"      容器完整性   : {'全部 SHA-256 校验通过' if verification['all_hashes_valid'] else '校验异常'} "
        f"| {mode_label}: {'已就绪' if verification['agb_firm_ready'] else '异常'}"
    )
    if result.preview_banner_png_path:
        print(f"      横幅预览图   : {result.preview_banner_png_path}")
    if result.preview_icon_png_path:
        print(f"      图标预览图   : {result.preview_icon_png_path}")
    print("=" * 68)
    print("提示: 将生成的 .cia 文件复制到 3DS SD 卡，使用 FBI 安装即可在桌面直接运行！")
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    target = Path(args.target).expanduser().resolve()
    if not target.is_file():
        print(f"[错误] 找不到文件: {target}", file=sys.stderr)
        return 1

    if target.suffix.lower() == ".cia":
        info = verify_and_inspect_cia(str(target))
        if args.json:
            if info.get("embedded_gba") is not None:
                info["embedded_gba"] = info["embedded_gba"].__dict__
            print(json.dumps(info, ensure_ascii=False, indent=2))
            return 0

        print("=" * 68)
        print(f"  3DS GBA Virtual Console CIA 检查报告: {target.name}")
        print("=" * 68)
        print(f"  文件路径       : {info['file_path']}")
        print(f"  文件大小       : {_format_size(info['file_size'])}")
        print(f"  Title ID       : {info['title_id']} (Product: {info['product_code']})")
        print(f"  CoreVersion    : {info['core_version']} (0x00000202 = AGB_FIRM 硬件 GBA 模式)")
        print(f"  SCI Flags      : {info['sci_flags']} (SDApplication=1, CompressExefsCode=0)")
        print(f"  SaveDataSize   : {_format_size(info['save_data_size'])}")
        print(f"  SHA-256 完整性 : {'通过 (TMD/NCCH/ExHeader/ExeFS 全部匹配)' if info['all_hashes_valid'] else '失败'}")
        print(f"  AGB_FIRM 就绪  : {'是' if info['agb_firm_ready'] else '否'}")
        if info.get("smdh"):
            s = info["smdh"]
            print(f"  SMDH 标题      : {s.get('short_title')} | 发行商: {s.get('publisher')}")
        if info.get("caa_footer"):
            c = info["caa_footer"]
            print(
                f"  .CAA 尾部配置  : Magic={c.get('magic')} | ROM偏移={c.get('rom_offset'):#x} | "
                f"ROM大小={_format_size(c.get('rom_size', 0))} | 存档=[0x{c.get('save_type_id', 0):02X}] {c.get('save_type_name')} | "
                f"残影={c.get('ghosting')}"
            )
        if info.get("embedded_gba"):
            g: GBARomInfo = info["embedded_gba"]
            print(
                f"  内嵌 GBA ROM   : 标题='{g.internal_title}' | 编号={g.game_code} | "
                f"厂商={g.maker_code} | 头部校验={'通过' if g.header_valid else '非标准'}"
            )
        print("=" * 68)
        return 0
    else:
        rom_info, _ = parse_rom_auto(str(target))
        if args.json:
            print(json.dumps(rom_info.__dict__, ensure_ascii=False, indent=2))
            return 0
        print("=" * 68)
        print(f"  {rom_info.system_name} ({rom_info.system.upper()}) ROM 检查报告: {target.name}")
        print("=" * 68)
        print(f"  文件路径       : {rom_info.file_path}")
        print(f"  系统平台       : {rom_info.system_name} ({rom_info.system.upper()})")
        print(f"  原始大小       : {_format_size(rom_info.raw_size)} (填充后: {_format_size(rom_info.padded_size)})")
        print(f"  内部标题       : {rom_info.internal_title}")
        print(f"  游戏编号       : {rom_info.game_code} ({rom_info.region}) | 厂商代码: {rom_info.maker_code}")
        print(f"  CRC32 / SHA1   : {rom_info.crc32_hex} / {rom_info.sha1}")
        print(f"  硬件/存档特征  : {', '.join(rom_info.sdk_save_strings) or '无'}")
        print(f"  3DS 运行模式   : [0x{rom_info.save_type_id:02X}] {rom_info.save_type_name}")
        print(f"  推荐 TitleID   : {rom_info.title_id_hex}")
        print("=" * 68)
        return 0


def cmd_extract_base(args: argparse.Namespace) -> int:
    saved = extract_base_templates_from_cia(args.cia_file)
    print("已成功从 CIA 提取模板:")
    for k, v in saved.items():
        print(f"  - {k}: {v}")
    return 0


def cmd_save(args: argparse.Namespace) -> int:
    res = convert_save_for_3ds(
        target_rom_or_cia=args.target,
        input_save_path=args.input_save,
        output_save_path=args.output,
        swap_eeprom=args.swap_eeprom,
    )
    if args.json:
        print(json.dumps(res.__dict__, ensure_ascii=False, indent=2))
        return 0

    print("=" * 68)
    print("  3DS GBA Virtual Console (AGB_FIRM) 存档转换与导入向导")
    print("=" * 68)
    print(f"  游戏编号 / TitleID : {res.game_code} / {res.title_id_hex}")
    print(f"  硬件存档类型       : [0x{res.save_type_id:02X}] {res.save_type_name}")
    print(f"  输入存档文件       : {res.input_save_path} ({_format_size(res.input_size)})")
    print(f"  输出 3DS 存档文件  : {res.output_save_path} ({_format_size(res.output_size)})")
    if res.rtc_footer_stripped:
        print("  处理说明           : 已自动剥离模拟器附加的 RTC 时间戳尾部")
    elif res.overpadding_stripped:
        print("  处理说明           : 已自动裁剪 RetroArch (.srm) 冗余填充字节至精确硬件大小")
    if res.eeprom_swapped:
        print("  EEPROM 字节序      : 已执行 8 字节 (64-bit) 字节序翻转")
    elif res.is_eeprom:
        print("  提示 (EEPROM)      : 若导入后游戏提示存档损坏，可加上 `--swap-eeprom` 参数重新转换")
    print("-" * 68)
    print("  【如何在 3DS 上导入该存档 (使用 GodMode9)】:")
    print(f"  1. 将生成的 `{Path(res.output_save_path).name}` 复制到 3DS SD 卡任意目录 (如 `/gm9/out/`)")
    print("  2. 在 3DS 上先启动一次该 GBA 游戏，看到开场画面后按 HOME 键退出")
    print("  3. 关机后按住 【Start】 键开机进入 GodMode9:")
    print(f"     a) 进入 `[0:] SDCARD` 找到 `{Path(res.output_save_path).name}`，按 `Y` 键复制 (Copy)")
    print("     b) 返回主菜单进入 `[S:] SYSNAND VIRTUAL` -> 选择 `agbsave.bin` -> 按 `A`")
    print("     c) 选择 `AGBSAVE options...` -> `Inject GBA VC save` -> 按 `A` 确认并按 `Start` 重启")
    print(f"  (注: 该游戏在 SD 卡上的加密存档路径为: `sdmc:/{res.sd_save_rel_path}`)")
    print("=" * 68)
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    if argv is None:
        argv = sys.argv[1:]

    # Allow shorthand `ciafoward <game.gba> [options]` without typing `build`
    if argv and not argv[0].startswith("-") and argv[0] not in SUBCOMMANDS:
        argv = ["build"] + list(argv)

    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "build":
        return cmd_build(args)
    if args.command in ("inspect", "info"):
        return cmd_inspect(args)
    if args.command == "extract-base":
        return cmd_extract_base(args)
    if args.command == "save":
        return cmd_save(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
