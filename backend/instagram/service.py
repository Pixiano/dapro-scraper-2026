"""Phase 8: best-effort Instagram profile fetch via instaloader (anonymous only).

Deliberately no login-session support — logged-in scraping risks getting that
account banned. Every failure mode returns a typed `available: False` payload
instead of raising; Instagram blocks anonymous scraping aggressively (especially
from datacenter IPs), so failure is an expected state, not an error."""


def fetch_profile(username: str) -> dict:
    try:
        import instaloader
    except ImportError:
        return {
            "available": False,
            "reason": "instaloaderNotInstalled",
            "message": "Run `pip install instaloader` and restart the server.",
        }
    try:
        loader = instaloader.Instaloader(
            quiet=True,
            download_pictures=False,
            download_videos=False,
            download_video_thumbnails=False,
            save_metadata=False,
        )
        profile = instaloader.Profile.from_username(loader.context, username)
        return {
            "available": True,
            "username": profile.username,
            "fullName": profile.full_name,
            "followers": profile.followers,
            "following": profile.followees,
            "posts": profile.mediacount,
            "bio": profile.biography,
            "verified": profile.is_verified,
            "private": profile.is_private,
        }
    except Exception as exc:  # rate limits, profile-not-found, layout changes, ...
        return {
            "available": False,
            "reason": exc.__class__.__name__,
            "message": str(exc)[:300] or "Instagram data unavailable — try again later.",
        }
