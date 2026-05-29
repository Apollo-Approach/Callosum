import os
import time
import threading
from pathlib import Path
import pystray
from PIL import Image, ImageDraw
from pystray import MenuItem as item
from .config import CallosumConfig

# For the background sweeper
from .scheduler import sweep_all

# Global variables
icon = None
scheduler_thread = None
running = True
config = CallosumConfig()
SWEEP_INTERVAL_HOURS = 4


def create_image():
    """Create a basic simple icon for Callosum."""
    # Create a 64x64 image with a dark background
    image = Image.new("RGB", (64, 64), color=(30, 30, 40))
    draw = ImageDraw.Draw(image)
    # Draw a stylized 'C' representing the corpus callosum
    draw.arc((10, 10, 54, 54), start=45, end=315, fill=(100, 200, 255), width=8)
    return image


def sweep_now(icon, item):
    """Trigger the garbage collection and ingestion."""
    icon.notify("Starting background sweep...", "Callosum")
    try:
        sweep_all(palace_path=config.palace_path, workspaces_dir=str(config.workspaces_dir))
        icon.notify("Sweep completed successfully.", "Callosum")
    except Exception as e:
        icon.notify(f"Sweep failed: {str(e)}", "Callosum")


def open_palace_dir(icon, item):
    """Open the palace data directory in Windows Explorer."""
    path = config.palace_path
    if os.path.exists(path):
        os.startfile(path)


def quit_action(icon, item):
    """Exit the tray app and stop background threads."""
    global running
    running = False
    icon.stop()


def background_sweeper():
    """Background thread that runs the sweeper every N hours."""
    global running
    while running:
        # Sleep in small chunks so we can exit quickly
        for _ in range(SWEEP_INTERVAL_HOURS * 3600):
            if not running:
                break
            time.sleep(1)

        if running:
            # We don't want to spam notifications for scheduled sweeps, just do it silently
            try:
                sweep_all(palace_path=config.palace_path, workspaces_dir=str(config.workspaces_dir))
            except Exception as e:
                print(f"Background sweep error: {e}")


def run_tray_app():
    global icon, scheduler_thread, running
    running = True

    menu = (
        item("Sweep Now", sweep_now),
        item("Open Palace Directory", open_palace_dir),
        item("Exit", quit_action),
    )

    # Try to load existing logo, fallback to drawn image
    try:
        logo_path = Path(__file__).parent.parent / "assets" / "callosum_logo_192x191.png"
        if logo_path.exists():
            image = Image.open(logo_path)
        else:
            image = create_image()
    except Exception:
        image = create_image()

    icon = pystray.Icon("Callosum", image, "Callosum Background Service", menu)

    # Start the background scheduling thread
    scheduler_thread = threading.Thread(target=background_sweeper, daemon=True)
    scheduler_thread.start()

    # This is a blocking call
    icon.run()


if __name__ == "__main__":
    run_tray_app()
