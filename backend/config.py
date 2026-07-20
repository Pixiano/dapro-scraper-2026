from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env", env_file_encoding="utf-8", extra="ignore"
    )

    youtube_api_key: str = ""
    db_path: Path = BASE_DIR / "data" / "app.db"

    quota_daily_budget: int = 10_000
    quota_soft_stop: int = 9_500

    ttl_snapshot_s: int = 6 * 3600
    ttl_list_s: int = 24 * 3600
    ttl_captions_s: int = 24 * 3600
    ttl_static_s: int = 30 * 24 * 3600
    ttl_negative_s: int = 3600

    rate_fresh: str = "10/minute"
    rate_cached: str = "60/minute"
    rate_expensive: str = "3/minute"

    enable_instagram: bool = False

    # --- V2: local LLM (llama.cpp) ---
    lmstudio_models_dir: Path = Path.home() / ".lmstudio" / "models"
    llama_server_exe: Path = BASE_DIR / "tools" / "llamacpp" / "llama-server.exe"

    # Model files, relative to lmstudio_models_dir unless absolute.
    # Vision: Qwen3.5-9B-Q8 chosen over gemma-4-12B on a real-screenshot bench —
    # Gemma hallucinated channel names and misread "451 views" as "451K".
    # Fallback: gemma-4-12B-it-QAT-Q4_0 + mmproj-gemma-4-12B-it-QAT-BF16.
    llm_vision_model: str = "lmstudio-community/Qwen3.5-9B-GGUF/Qwen3.5-9B-Q8_0.gguf"
    llm_vision_mmproj: str = "lmstudio-community/Qwen3.5-9B-GGUF/mmproj-Qwen3.5-9B-BF16.gguf"
    # Synthesis model: gpt-oss-20b (reasoning, Apache-2.0). Fallback: Qwen3-14B-Q4_K_M.
    llm_text_model: str = "lmstudio-community/gpt-oss-20b-GGUF/gpt-oss-20b.gguf"
    llm_reasoning_effort: str = "high"  # gpt-oss reasoning depth for synthesis

    llm_host: str = "127.0.0.1"
    llm_port: int = 8080
    llm_ctx: int = 8192          # vision: one image + prompt fits comfortably
    llm_ctx_text: int = 40960    # synthesis: briefs from many sources + reasoning + dossier (40k)
    llm_ngl: int = 99  # GPU layers to offload; 99 = all
    llm_start_timeout_s: int = 180
    models_sequential: bool = True  # 16 GB VRAM → one model resident at a time

    # --- V2: WebsiteCollector ---
    site_page_cap: int = 5          # pages per site incl. main (screenshot each)
    site_image_cap: int = 6         # meaningful images downloaded per site
    site_text_cap: int = 20000      # chars of extracted text per page
    site_nav_timeout_ms: int = 20000

    # --- V2: YouTubeCollector (content reframe) ---
    yt_videos_recent: int = 10      # recent videos: title + description
    yt_transcript_videos: int = 5   # of those, fetch transcripts for the first N
    yt_transcript_char_cap: int = 8000
    yt_comments_videos: int = 5     # fetch top comments for the first N videos
    yt_comments_per_video: int = 10
    yt_thumb_images: int = 4        # video thumbnails saved for the vision stage

    # --- V2: SocialCollector (3 methods, best-effort) ---
    social_nav_timeout_ms: int = 20000
    social_text_cap: int = 8000

    # --- V2b: additional bespoke collectors ---
    github_repos: int = 20            # top repos listed (by stars)
    github_readme_chars: int = 6000   # profile/top-repo README text cap
    medium_articles: int = 15         # recent Medium posts from the feed
    substack_posts: int = 15          # recent Substack posts from the feed
    reddit_items: int = 25            # recent Reddit posts fetched
    news_items: int = 15              # Google News headlines per entity
    enable_news: bool = True          # auto-add an entity-name news feed
    linktree_links_cap: int = 40      # discovered outbound links kept in facts
    social_page_wait_ms: int = 1800   # JS settle time for Playwright renders

    # --- V2: Vision stage ---
    vision_images_per_source: int = 8   # screenshots first, then images
    vision_max_tokens: int = 800
    # 1024 measured markedly better on real screenshots: at the default (~258
    # image tokens) Qwen garbled channel names ("Rinks Labs", "MrWhoseBoss");
    # at 1024 it read them exactly. Costs ~15% more time per image.
    vision_min_image_tokens: int = 1024
    vision_skip_blank: bool = True      # logged-out social shots are often blank
    vision_blank_stddev: float = 3.0

    # --- V2: Synthesis stage (map-reduce via gpt-oss) ---
    synthesis_chunk_chars: int = 6000   # per map call (~1.5-2k tokens)
    # gpt-oss at high reasoning spends tokens THINKING before the answer, so these
    # budgets must cover reasoning + output. Raised to survive 6+ source jobs where
    # the dossier body (findings-per-source) grows with each link.
    synthesis_map_tokens: int = 1300
    synthesis_brief_tokens: int = 2000
    synthesis_dossier_tokens: int = 10000

    def model_path(self, rel: str) -> Path:
        p = Path(rel)
        return p if p.is_absolute() else self.lmstudio_models_dir / p


settings = Settings()
