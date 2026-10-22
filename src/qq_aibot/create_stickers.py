from pathlib import Path
from PIL import Image, ImageDraw, ImageFont

def create_bundled_stickers(sticker_dir: Path) -> None:
    sticker_dir.mkdir(parents=True, exist_ok=True)

    stickers = [
        ("happy", "😊", (255, 220, 100)),
        ("laugh", "😄", (255, 200, 80)),
        ("think", "🤔", (200, 200, 220)),
        ("confused", "😕", (220, 200, 180)),
        ("cry", "😢", (180, 200, 255)),
        ("angry", "😠", (255, 180, 180)),
        ("cool", "😎", (180, 220, 255)),
        ("surprised", "😲", (255, 220, 180)),
    ]

    size = 128
    for sticker_id, emoji, bg_color in stickers:
        img = Image.new('RGB', (size, size), color=bg_color)
        draw = ImageDraw.Draw(img)

        try:
            font = ImageFont.truetype("seguiemj.ttf", 72)
        except:
            try:
                font = ImageFont.truetype("arial.ttf", 72)
            except:
                font = ImageFont.load_default()

        bbox = draw.textbbox((0, 0), emoji, font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]
        x = (size - text_width) // 2 - bbox[0]
        y = (size - text_height) // 2 - bbox[1]

        draw.text((x, y), emoji, fill=(50, 50, 50), font=font)

        output_path = sticker_dir / f"{sticker_id}.png"
        img.save(output_path, 'PNG')
        print(f"Created: {output_path}")

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
    else:
        target = Path(__file__).parent / "stickers"
    create_bundled_stickers(target)
    print(f"All stickers created in {target}")
