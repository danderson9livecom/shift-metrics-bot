import os
import time
import json
import math
import csv
import requests
import smtplib
from email.message import EmailMessage
from urllib.parse import urlparse
from datetime import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from twilio.rest import Client

"""
SHIFT MLB V3.10.1
V4 FOUNDATION DATABASE INTEGRITY PATCH

Current Production Version

This codebase is cumulative. It includes the earlier V3.5, V3.6,
V3.7, V3.8, and V3.9 engines, plus the V3.10 evaluation layer.
Seeing older version labels inside feature-specific comments is normal;
those sections remain active parts of the current model.

Major V3.10 Business-Process Additions:
- Google Sheets long-term memory through TRACKING_WEBHOOK_URL
- Master decision database for BET_NOW, TEST_UNIT, RESEARCH_ONLY, and NO_BET
- Adaptive profile learning through adaptive_config.json
- Feature learning summaries
- Rolling 7-day / 30-day / season evaluation dashboards
- Better CLV snapshot tracking
- Capped adaptive confidence adjustments
- Daily learning reports with stronger evaluation sections

Core Purpose:
SHIFT is not just an alert bot. It is a season-long research platform.
It tracks every meaningful model decision, grades outcomes, measures CLV,
identifies which profiles are profitable, and gradually improves confidence
through sample-based learning.

Operational Goal:
Game -> Decision -> Google Sheets Database -> Grading -> Learning -> Dashboard -> Better Decisions

Important:
The bot can only evaluate odds markets returned by the configured odds provider.
Some Odds API plans do not provide true in-play/live totals, team totals, or
remaining-game totals. Premium providers can be added later through environment
switches without rewriting the betting engine.
"""

load_dotenv()

# ---------------------------------------------------------------------------
# SHIFT deployment identity / anti-confusion banner
# ---------------------------------------------------------------------------
APP_VERSION = os.getenv("SHIFT_APP_VERSION", "V3.10.1")
APP_MODE = "V4 FOUNDATION DATABASE INTEGRITY PATCH"
APP_BUILD_LABEL = f"SHIFT MLB {APP_VERSION} {APP_MODE}"
DEPLOY_MARKER = os.getenv("DEPLOY_MARKER", f"{APP_VERSION}-v4-foundation-database-integrity")
RUN_EMAIL_TEST_ON_START = os.getenv("RUN_EMAIL_TEST_ON_START", "false").lower() == "true"

TZ = ZoneInfo("America/Phoenix")
STATE_FILE = os.getenv("STATE_FILE", "shift_v36_state.json")

STRIKE_HISTORY_FILE = os.getenv("STRIKE_HISTORY_FILE", "strike_history.csv")
CLV_HISTORY_FILE = os.getenv("CLV_HISTORY_FILE", "clv_history.csv")
GRADED_RESULTS_FILE = os.getenv("GRADED_RESULTS_FILE", "graded_results.csv")
LEARNING_SUMMARY_FILE = os.getenv("LEARNING_SUMMARY_FILE", "learning_summary.csv")
PROFILE_NEAR_MISS_FILE = os.getenv("PROFILE_NEAR_MISS_FILE", "profile_near_misses.csv")
PROFILE_RESEARCH_FILE = os.getenv("PROFILE_RESEARCH_FILE", "profile_research_candidates.csv")
PROFILE_LEARNING_SUMMARY_FILE = os.getenv("PROFILE_LEARNING_SUMMARY_FILE", "profile_learning_summary.csv")

# V3.9.0 decision database + adaptive learning engine.
# This logs EVERY meaningful model decision, not only sent BET NOW alerts.
# It is the bridge from daily reporting into long-term evaluation and controlled self-optimization.
DECISION_LOG_FILE = os.getenv("DECISION_LOG_FILE", "shift_decision_log.csv")
ADAPTIVE_CONFIG_FILE = os.getenv("ADAPTIVE_CONFIG_FILE", "adaptive_config.json")
ENABLE_DECISION_LOG = os.getenv("ENABLE_DECISION_LOG", "true").lower() == "true"
ENABLE_DECISION_LOG_NO_BETS = os.getenv("ENABLE_DECISION_LOG_NO_BETS", "true").lower() == "true"
ENABLE_DECISION_LOG_RESEARCH = os.getenv("ENABLE_DECISION_LOG_RESEARCH", "true").lower() == "true"
ENABLE_ADAPTIVE_CONFIG = os.getenv("ENABLE_ADAPTIVE_CONFIG", "true").lower() == "true"
ENABLE_ADAPTIVE_CONFIDENCE = os.getenv("ENABLE_ADAPTIVE_CONFIDENCE", "true").lower() == "true"
ENABLE_ADAPTIVE_REPORTING = os.getenv("ENABLE_ADAPTIVE_REPORTING", "true").lower() == "true"
MIN_ADAPTIVE_SAMPLE = int(os.getenv("MIN_ADAPTIVE_SAMPLE", "75"))
ADAPTIVE_STRONG_ROI = float(os.getenv("ADAPTIVE_STRONG_ROI", "0.04"))
ADAPTIVE_WEAK_ROI = float(os.getenv("ADAPTIVE_WEAK_ROI", "-0.03"))
ADAPTIVE_STRONG_CLV = float(os.getenv("ADAPTIVE_STRONG_CLV", "0.25"))
ADAPTIVE_WEAK_CLV = float(os.getenv("ADAPTIVE_WEAK_CLV", "-0.25"))
ADAPTIVE_PROVEN_CONF_BONUS = int(os.getenv("ADAPTIVE_PROVEN_CONF_BONUS", "4"))
ADAPTIVE_TIGHTEN_CONF_PENALTY = int(os.getenv("ADAPTIVE_TIGHTEN_CONF_PENALTY", "6"))
ADAPTIVE_FAILING_CONF_PENALTY = int(os.getenv("ADAPTIVE_FAILING_CONF_PENALTY", "10"))
DECISION_LOG_REJECT_COOLDOWN_SECONDS = int(os.getenv("DECISION_LOG_REJECT_COOLDOWN_SECONDS", "900"))
DECISION_LOG_ACCEPT_COOLDOWN_SECONDS = int(os.getenv("DECISION_LOG_ACCEPT_COOLDOWN_SECONDS", "300"))

ENABLE_SELF_LEARNING = os.getenv("ENABLE_SELF_LEARNING", "true").lower() == "true"

# V3.7.3 profile learning / calculated-risk controls.
ENABLE_PROFILE_LEARNING_GATES = os.getenv("ENABLE_PROFILE_LEARNING_GATES", "true").lower() == "true"
ENABLE_PROFILE_NEAR_MISS_LOG = os.getenv("ENABLE_PROFILE_NEAR_MISS_LOG", "true").lower() == "true"
PROFILE_MIN_SAMPLE_TO_TIGHTEN = int(os.getenv("PROFILE_MIN_SAMPLE_TO_TIGHTEN", "30"))
PROFILE_MIN_SAMPLE_TO_AUTO_ADJUST = int(os.getenv("PROFILE_MIN_SAMPLE_TO_AUTO_ADJUST", "75"))
PROFILE_WEAK_WIN_PCT = float(os.getenv("PROFILE_WEAK_WIN_PCT", "52"))
PROFILE_STRONG_WIN_PCT = float(os.getenv("PROFILE_STRONG_WIN_PCT", "57"))
PROFILE_TARGET_CLV = float(os.getenv("PROFILE_TARGET_CLV", "0.25"))
PROFILE_TIGHTEN_CONF_BUMP = int(os.getenv("PROFILE_TIGHTEN_CONF_BUMP", "4"))
PROFILE_LOOSEN_CONF_CREDIT = int(os.getenv("PROFILE_LOOSEN_CONF_CREDIT", "2"))
CALCULATED_RISK_TIER_A_CONF = int(os.getenv("CALCULATED_RISK_TIER_A_CONF", "82"))
CALCULATED_RISK_TIER_B_CONF = int(os.getenv("CALCULATED_RISK_TIER_B_CONF", "72"))
CALCULATED_RISK_TIER_C_CONF = int(os.getenv("CALCULATED_RISK_TIER_C_CONF", "68"))

# V3.7.4 philosophy: no daily caps. Opportunity is evaluated per game, not per day.
# Risk is controlled through quality gates, same-game repeat protection, tiering,
# and later profile-specific tightening after samples develop.
NO_DAILY_PROFILE_CAPS = os.getenv("NO_DAILY_PROFILE_CAPS", "true").lower() == "true"
ENABLE_TIER_UNIT_GUIDANCE = os.getenv("ENABLE_TIER_UNIT_GUIDANCE", "true").lower() == "true"
TIER_A_UNIT_LABEL = os.getenv("TIER_A_UNIT_LABEL", "FULL UNIT")
TIER_B_UNIT_LABEL = os.getenv("TIER_B_UNIT_LABEL", "HALF UNIT")
TIER_C_UNIT_LABEL = os.getenv("TIER_C_UNIT_LABEL", "TEST UNIT")
TIER_WATCH_UNIT_LABEL = os.getenv("TIER_WATCH_UNIT_LABEL", "NO BET / LOG ONLY")

# V3.7.6 profile-promotion logic:
# Logs showed the engine detects CONTINUATION_OVER and INFLATED_UNDER,
# but the old generic confidence gate was not promoting enough profile-qualified
# opportunities. These thresholds let each profile compete on its own terms.
ENABLE_PROFILE_PROMOTION_LOGIC = os.getenv("ENABLE_PROFILE_PROMOTION_LOGIC", "true").lower() == "true"
PROFILE_PROMOTE_DISCOUNTED_OVER_CONF = int(os.getenv("PROFILE_PROMOTE_DISCOUNTED_OVER_CONF", "68"))
PROFILE_PROMOTE_CONTINUATION_OVER_CONF = int(os.getenv("PROFILE_PROMOTE_CONTINUATION_OVER_CONF", "70"))
PROFILE_PROMOTE_INFLATED_UNDER_CONF = int(os.getenv("PROFILE_PROMOTE_INFLATED_UNDER_CONF", "70"))
PROFILE_PROMOTE_FALSE_INFLATION_CONF = int(os.getenv("PROFILE_PROMOTE_FALSE_INFLATION_CONF", "72"))
PROFILE_PROMOTE_PITCHING_DOM_UNDER_CONF = int(os.getenv("PROFILE_PROMOTE_PITCHING_DOM_UNDER_CONF", "74"))
PROFILE_PROMOTE_MIN_EDGE = float(os.getenv("PROFILE_PROMOTE_MIN_EDGE", "0.75"))
PROFILE_PROMOTE_MIN_VALUE = int(os.getenv("PROFILE_PROMOTE_MIN_VALUE", "62"))
PROFILE_PROMOTE_MAX_RISK = int(os.getenv("PROFILE_PROMOTE_MAX_RISK", "58"))
PROFILE_PROMOTE_CONTINUATION_MIN_SCORE = int(os.getenv("PROFILE_PROMOTE_CONTINUATION_MIN_SCORE", "82"))
PROFILE_PROMOTE_INFLATED_MIN_SETTLE = int(os.getenv("PROFILE_PROMOTE_INFLATED_MIN_SETTLE", "70"))
PROFILE_PROMOTE_FALSE_MIN_SCORE = int(os.getenv("PROFILE_PROMOTE_FALSE_MIN_SCORE", "55"))
PROFILE_PROMOTE_PITCHING_MIN_SCORE = int(os.getenv("PROFILE_PROMOTE_PITCHING_MIN_SCORE", "72"))
PROFILE_PROMOTE_DISCOUNTED_MIN_SCORE = int(os.getenv("PROFILE_PROMOTE_DISCOUNTED_MIN_SCORE", "60"))
PROFILE_PROMOTE_CONTINUATION_MIN_P2R = int(os.getenv("PROFILE_PROMOTE_CONTINUATION_MIN_P2R", "80"))
PROFILE_PROMOTE_CONTINUATION_MIN_CONV = int(os.getenv("PROFILE_PROMOTE_CONTINUATION_MIN_CONV", "70"))
PROFILE_PROMOTE_MAX_LINE_AGE = int(os.getenv("PROFILE_PROMOTE_MAX_LINE_AGE", "120"))

# V3.7.8 continuation-over exhaustion controls:
# Recent reports showed DISCOUNTED_OVER is strong, while CONTINUATION_OVER is noisy
# when the live total has already climbed several runs. These gates keep
# calculated risk, but require elite continuation evidence when the market has
# already reacted aggressively upward.
ENABLE_CONTINUATION_EXHAUSTION = os.getenv("ENABLE_CONTINUATION_EXHAUSTION", "true").lower() == "true"
CONT_EXHAUSTION_CAUTION_SCORE = int(os.getenv("CONT_EXHAUSTION_CAUTION_SCORE", "55"))
CONT_EXHAUSTION_DANGER_SCORE = int(os.getenv("CONT_EXHAUSTION_DANGER_SCORE", "75"))
CONT_EXHAUSTION_MOVE_CAUTION = float(os.getenv("CONT_EXHAUSTION_MOVE_CAUTION", "3.0"))
CONT_EXHAUSTION_MOVE_DANGER = float(os.getenv("CONT_EXHAUSTION_MOVE_DANGER", "4.0"))
CONT_EXHAUSTION_CAUTION_MIN_P2R = int(os.getenv("CONT_EXHAUSTION_CAUTION_MIN_P2R", "88"))
CONT_EXHAUSTION_CAUTION_MIN_CONV = int(os.getenv("CONT_EXHAUSTION_CAUTION_MIN_CONV", "82"))
CONT_EXHAUSTION_CAUTION_MIN_PRESSURE = int(os.getenv("CONT_EXHAUSTION_CAUTION_MIN_PRESSURE", "55"))
CONT_EXHAUSTION_DANGER_MIN_P2R = int(os.getenv("CONT_EXHAUSTION_DANGER_MIN_P2R", "95"))
CONT_EXHAUSTION_DANGER_MIN_CONV = int(os.getenv("CONT_EXHAUSTION_DANGER_MIN_CONV", "90"))
CONT_EXHAUSTION_DANGER_MIN_PRESSURE = int(os.getenv("CONT_EXHAUSTION_DANGER_MIN_PRESSURE", "70"))
CONT_EXHAUSTION_DANGER_MIN_EDGE = float(os.getenv("CONT_EXHAUSTION_DANGER_MIN_EDGE", "4.0"))
CONT_EXHAUSTION_DANGER_MAX_RISK = int(os.getenv("CONT_EXHAUSTION_DANGER_MAX_RISK", "30"))

# V3.7.8 profile-strength / historical profile boost controls.
# These are intentionally sample-disciplined: they help identify proven profiles
# without letting tiny samples rewrite the model.
ENABLE_PROFILE_STRENGTH_TIERING = os.getenv("ENABLE_PROFILE_STRENGTH_TIERING", "true").lower() == "true"
PROFILE_STRENGTH_TIER_A = int(os.getenv("PROFILE_STRENGTH_TIER_A", "85"))
PROFILE_STRENGTH_TIER_B = int(os.getenv("PROFILE_STRENGTH_TIER_B", "70"))
PROFILE_HISTORY_BOOST_MIN_SAMPLE = int(os.getenv("PROFILE_HISTORY_BOOST_MIN_SAMPLE", "8"))
PROFILE_HISTORY_STRONG_WIN_PCT = float(os.getenv("PROFILE_HISTORY_STRONG_WIN_PCT", "60"))
PROFILE_HISTORY_WEAK_WIN_PCT = float(os.getenv("PROFILE_HISTORY_WEAK_WIN_PCT", "52"))
PROFILE_HISTORY_STRONG_BOOST = int(os.getenv("PROFILE_HISTORY_STRONG_BOOST", "4"))
PROFILE_HISTORY_WEAK_PENALTY = int(os.getenv("PROFILE_HISTORY_WEAK_PENALTY", "4"))

# V3.7.9 professional discipline controls:
# Discounted OVER is currently the strongest profile, so do not rewrite it.
# Instead, protect it from weak late-game spots and identify elite Tier A setups.
ENABLE_DISCOUNTED_OVER_LATE_DISCIPLINE = os.getenv("ENABLE_DISCOUNTED_OVER_LATE_DISCIPLINE", "true").lower() == "true"
DISCOUNTED_OVER_LATE_INNING = int(os.getenv("DISCOUNTED_OVER_LATE_INNING", "7"))
DISCOUNTED_OVER_LATE_MAX_RISK = int(os.getenv("DISCOUNTED_OVER_LATE_MAX_RISK", "39"))
DISCOUNTED_OVER_LATE_MIN_EDGE = float(os.getenv("DISCOUNTED_OVER_LATE_MIN_EDGE", "4.0"))
DISCOUNTED_OVER_LATE_MIN_THREAT = int(os.getenv("DISCOUNTED_OVER_LATE_MIN_THREAT", "80"))
DISCOUNTED_OVER_LATE_MIN_TRAFFIC = int(os.getenv("DISCOUNTED_OVER_LATE_MIN_TRAFFIC", "75"))
DISCOUNTED_OVER_LATE_MIN_P2R = int(os.getenv("DISCOUNTED_OVER_LATE_MIN_P2R", "90"))
DISCOUNTED_OVER_LATE_MIN_CONV = int(os.getenv("DISCOUNTED_OVER_LATE_MIN_CONV", "85"))

ENABLE_DISCOUNTED_OVER_TIER_A = os.getenv("ENABLE_DISCOUNTED_OVER_TIER_A", "true").lower() == "true"
DISCOUNTED_OVER_TIER_A_SCORE = int(os.getenv("DISCOUNTED_OVER_TIER_A_SCORE", "78"))
DISCOUNTED_OVER_TIER_A_VALUE = int(os.getenv("DISCOUNTED_OVER_TIER_A_VALUE", "95"))
DISCOUNTED_OVER_TIER_A_MAX_RISK = int(os.getenv("DISCOUNTED_OVER_TIER_A_MAX_RISK", "10"))
DISCOUNTED_OVER_TIER_A_EDGE = float(os.getenv("DISCOUNTED_OVER_TIER_A_EDGE", "6.0"))
DISCOUNTED_OVER_TIER_A_MAX_INNING = int(os.getenv("DISCOUNTED_OVER_TIER_A_MAX_INNING", "6"))

# Research mode: log rare UNDER profile candidates even when they do not send SMS.
# This lets us learn whether INFLATED_UNDER and PITCHING_DOMINANCE_UNDER are real edges.
ENABLE_PROFILE_RESEARCH_DATABASE = os.getenv("ENABLE_PROFILE_RESEARCH_DATABASE", "true").lower() == "true"
PROFILE_RESEARCH_PITCHING_MIN_SCORE = int(os.getenv("PROFILE_RESEARCH_PITCHING_MIN_SCORE", "55"))
PROFILE_RESEARCH_LOG_DISCOUNTED_OVER = os.getenv("PROFILE_RESEARCH_LOG_DISCOUNTED_OVER", "true").lower() == "true"
PROFILE_RESEARCH_LOG_UNDER_CANDIDATES = os.getenv("PROFILE_RESEARCH_LOG_UNDER_CANDIDATES", "true").lower() == "true"

# V3.8.0 controlled UNDER profile test alerts:
# These make INFLATED_UNDER and PITCHING_DOMINANCE_UNDER fire as Tier C / TEST UNIT
# so the bot can collect real promoted samples and learn without overexposing bankroll.
ENABLE_UNDER_PROFILE_TEST_ALERTS = os.getenv("ENABLE_UNDER_PROFILE_TEST_ALERTS", "true").lower() == "true"
UNDER_TEST_FORCE_TIER_C = os.getenv("UNDER_TEST_FORCE_TIER_C", "true").lower() == "true"
UNDER_TEST_UNIT_LABEL = os.getenv("UNDER_TEST_UNIT_LABEL", "TEST UNIT")

INFLATED_UNDER_TEST_MIN_SETTLE = int(os.getenv("INFLATED_UNDER_TEST_MIN_SETTLE", "68"))
INFLATED_UNDER_TEST_MAX_CONTINUATION = int(os.getenv("INFLATED_UNDER_TEST_MAX_CONTINUATION", "72"))
INFLATED_UNDER_TEST_MIN_MOVE = float(os.getenv("INFLATED_UNDER_TEST_MIN_MOVE", "2.0"))
INFLATED_UNDER_TEST_MIN_EDGE = float(os.getenv("INFLATED_UNDER_TEST_MIN_EDGE", "0.50"))
INFLATED_UNDER_TEST_MAX_RISK = int(os.getenv("INFLATED_UNDER_TEST_MAX_RISK", "55"))
INFLATED_UNDER_TEST_MAX_PRESSURE = int(os.getenv("INFLATED_UNDER_TEST_MAX_PRESSURE", "75"))
INFLATED_UNDER_TEST_MAX_TRAFFIC = int(os.getenv("INFLATED_UNDER_TEST_MAX_TRAFFIC", "82"))

PITCHING_DOMINANCE_TEST_MIN_SCORE = int(os.getenv("PITCHING_DOMINANCE_TEST_MIN_SCORE", "62"))
PITCHING_DOMINANCE_TEST_OPEN_MAX = float(os.getenv("PITCHING_DOMINANCE_TEST_OPEN_MAX", "8.5"))
PITCHING_DOMINANCE_TEST_MIN_INNING = int(os.getenv("PITCHING_DOMINANCE_TEST_MIN_INNING", "2"))
PITCHING_DOMINANCE_TEST_MAX_INNING = int(os.getenv("PITCHING_DOMINANCE_TEST_MAX_INNING", "5"))
PITCHING_DOMINANCE_TEST_MAX_RUNS = int(os.getenv("PITCHING_DOMINANCE_TEST_MAX_RUNS", "2"))
PITCHING_DOMINANCE_TEST_MAX_LIVE_DROP = float(os.getenv("PITCHING_DOMINANCE_TEST_MAX_LIVE_DROP", "1.75"))
PITCHING_DOMINANCE_TEST_MAX_CONTACT = int(os.getenv("PITCHING_DOMINANCE_TEST_MAX_CONTACT", "55"))
PITCHING_DOMINANCE_TEST_MAX_STRESS = int(os.getenv("PITCHING_DOMINANCE_TEST_MAX_STRESS", "65"))
PITCHING_DOMINANCE_TEST_MAX_P2R = int(os.getenv("PITCHING_DOMINANCE_TEST_MAX_P2R", "82"))
PITCHING_DOMINANCE_TEST_MIN_EDGE = float(os.getenv("PITCHING_DOMINANCE_TEST_MIN_EDGE", "0.35"))

# V3.7.7 neutral-market discipline:
# June 5-7 reports showed NEUTRAL_MARKET was the major leak.
# Keep it classified and reported, but do not allow it to become a BET NOW SMS.
ENABLE_NEUTRAL_MARKET_WATCH_ONLY = os.getenv("ENABLE_NEUTRAL_MARKET_WATCH_ONLY", "true").lower() == "true"
NEUTRAL_MARKET_WATCH_REASON = "NEUTRAL_MARKET demoted to research/log-only; no market overreaction or underreaction edge"

# User-entry tracking placeholders. These do not change betting logic; they make
# it possible to compare bot line vs. actual user entry when the user beats the number.
ENABLE_ACTUAL_ENTRY_TRACKING = os.getenv("ENABLE_ACTUAL_ENTRY_TRACKING", "true").lower() == "true"
ACTUAL_ENTRY_FILE = os.getenv("ACTUAL_ENTRY_FILE", "actual_entries.csv")

# Daily learning report:
# Prints a summary to Railway logs after games finish and optionally texts it.
ENABLE_DAILY_LEARNING_REPORT = os.getenv("ENABLE_DAILY_LEARNING_REPORT", "true").lower() == "true"
SEND_DAILY_LEARNING_REPORT_SMS = os.getenv("SEND_DAILY_LEARNING_REPORT_SMS", "true").lower() == "true"

# V3.6 nightly email reporting.
# SMS remains BET NOW only. Email becomes the full archive for analysis.
ENABLE_NIGHTLY_EMAIL_REPORT = os.getenv("ENABLE_NIGHTLY_EMAIL_REPORT", "true").lower() == "true"
NIGHTLY_EMAIL_TO = os.getenv("NIGHTLY_EMAIL_TO", "danderson9@live.com").strip()
NIGHTLY_EMAIL_SUBJECT_PREFIX = os.getenv("NIGHTLY_EMAIL_SUBJECT_PREFIX", "SHIFT MLB Daily Summary").strip()
EMAIL_FROM = os.getenv("EMAIL_FROM", os.getenv("SMTP_USER", "")).strip()
SMTP_HOST = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER = os.getenv("SMTP_USER", "").strip()
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "").strip()
SMTP_USE_TLS = os.getenv("SMTP_USE_TLS", "true").lower() == "true"
ATTACH_DAILY_CSVS_TO_EMAIL = os.getenv("ATTACH_DAILY_CSVS_TO_EMAIL", "true").lower() == "true"

# V2.4.1 user-facing grading upgrade:
# Sends a short result text as soon as each completed STRIKE is graded.
# This is separate from the end-of-night daily learning report.
ENABLE_GRADED_RESULT_SMS = os.getenv("ENABLE_GRADED_RESULT_SMS", "true").lower() == "true"
ENABLE_GRADED_RESULT_LOG = os.getenv("ENABLE_GRADED_RESULT_LOG", "true").lower() == "true"
MAX_RESULT_SMS_CHARS = int(os.getenv("MAX_RESULT_SMS_CHARS", "900"))

# V2.4.2 persistent tracking / reporting upgrades:
# Optional generic webhook for Google Sheets Apps Script, Zapier, Make, Supabase Edge Function, etc.
# If blank, the bot still writes local CSV as before.
TRACKING_WEBHOOK_URL = os.getenv("TRACKING_WEBHOOK_URL", "").strip()
TRACKING_WEBHOOK_SECRET = os.getenv("TRACKING_WEBHOOK_SECRET", "").strip()
ENABLE_TRACKING_WEBHOOK = os.getenv("ENABLE_TRACKING_WEBHOOK", "false").lower() == "true"
ENABLE_CLV_POLL_SNAPSHOTS = os.getenv("ENABLE_CLV_POLL_SNAPSHOTS", "true").lower() == "true"
CLV_SNAPSHOT_MIN_MOVE = float(os.getenv("CLV_SNAPSHOT_MIN_MOVE", "0.5"))
ENABLE_ACTIONABLE_DAILY_RECOMMENDATIONS = os.getenv("ENABLE_ACTIONABLE_DAILY_RECOMMENDATIONS", "true").lower() == "true"
MIN_RECOMMENDATION_SAMPLE = int(os.getenv("MIN_RECOMMENDATION_SAMPLE", "2"))

DAILY_LEARNING_REPORT_HOUR = int(os.getenv("DAILY_LEARNING_REPORT_HOUR", "22"))
MIN_PATTERN_SAMPLE_FOR_REPORT = int(os.getenv("MIN_PATTERN_SAMPLE_FOR_REPORT", "30"))


ODDS_API_KEY = os.getenv("ODDS_API_KEY", "")
TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM_NUMBER = os.getenv("TWILIO_FROM_NUMBER", "")
ALERT_TO_NUMBER = os.getenv("ALERT_TO_NUMBER", "")

SLOW_POLL_SECONDS = int(os.getenv("SLOW_POLL_SECONDS", "300"))
ACTIVE_POLL_SECONDS = int(os.getenv("ACTIVE_POLL_SECONDS", "45"))
FAST_POLL_SECONDS = int(os.getenv("FAST_POLL_SECONDS", "20"))

PREGAME_WINDOW_MINUTES = int(os.getenv("PREGAME_WINDOW_MINUTES", "45"))

# V3.5 real-time data adapter controls.
# No paid premium feed is required for the default setup.
LIVE_GAME_PROVIDER = os.getenv("LIVE_GAME_PROVIDER", "mlb_stats_api").lower().strip()
ODDS_PROVIDER = os.getenv("ODDS_PROVIDER", "the_odds_api").lower().strip()
PREMIUM_DATA_PROVIDER = os.getenv("PREMIUM_DATA_PROVIDER", "none").lower().strip()
ENABLE_MLB_STATS_API_CONTEXT = os.getenv("ENABLE_MLB_STATS_API_CONTEXT", "true").lower() == "true"
SPORTSDATAIO_KEY = os.getenv("SPORTSDATAIO_KEY", "").strip()
OPTICODDS_KEY = os.getenv("OPTICODDS_KEY", "").strip()
SPORTRADAR_KEY = os.getenv("SPORTRADAR_KEY", "").strip()


# V2.1 separates OVER and UNDER thresholds.
# Unders often require a slightly smaller model edge because live markets can overinflate after early runs.
MIN_EDGE_RUNS = float(os.getenv("MIN_EDGE_RUNS", "0.9"))  # backward compatible fallback
MIN_OVER_EDGE_RUNS = float(os.getenv("MIN_OVER_EDGE_RUNS", "0.95"))
MIN_UNDER_EDGE_RUNS = float(os.getenv("MIN_UNDER_EDGE_RUNS", "0.70"))
MIN_WATCH_EDGE_RUNS = float(os.getenv("MIN_WATCH_EDGE_RUNS", "0.35"))
STRONG_EDGE_RUNS = float(os.getenv("STRONG_EDGE_RUNS", "1.4"))

MAX_PRICE_FAVORITE = int(os.getenv("MAX_PRICE_FAVORITE", "-140"))
MAX_PRICE_DOG = int(os.getenv("MAX_PRICE_DOG", "110"))

ALERT_COOLDOWN_SECONDS = int(os.getenv("ALERT_COOLDOWN_SECONDS", "720"))
EDGE_IMPROVEMENT_TO_REPEAT = float(os.getenv("EDGE_IMPROVEMENT_TO_REPEAT", "0.7"))

# Alert controls
SEND_WATCH_ALERTS = os.getenv("SEND_WATCH_ALERTS", "false").lower() == "true"
MAX_ALERTS_PER_GAME_SIDE = int(os.getenv("MAX_ALERTS_PER_GAME_SIDE", "1"))  # legacy compatibility only; V2.6 uses one thesis per game
LINE_IMPROVEMENT_TO_REPEAT = float(os.getenv("LINE_IMPROVEMENT_TO_REPEAT", "1.0"))

# V2.5 Professional Accuracy Mode:
# STRIKE texts should be rare, current, and decisive.
# The model can still evaluate every game, but SMS is only for BET NOW.
ENABLE_PRO_ACCURACY_MODE = os.getenv("ENABLE_PRO_ACCURACY_MODE", "true").lower() == "true"
ENABLE_CURRENT_RECOMMENDATION_STATE = os.getenv("ENABLE_CURRENT_RECOMMENDATION_STATE", "true").lower() == "true"
ENABLE_OPPOSITE_THESIS_LOCK = os.getenv("ENABLE_OPPOSITE_THESIS_LOCK", "true").lower() == "true"
ENABLE_SAME_THESIS_RESTRIKE = os.getenv("ENABLE_SAME_THESIS_RESTRIKE", "false").lower() == "true"
ENABLE_SHIFT_REVERSAL = os.getenv("ENABLE_SHIFT_REVERSAL", "true").lower() == "true"

# A true reversal is rare. It must be much stronger than the original thesis.
MIN_REVERSAL_INNINGS_PASSED = float(os.getenv("MIN_REVERSAL_INNINGS_PASSED", "2.0"))
MIN_REVERSAL_CONFIDENCE = int(os.getenv("MIN_REVERSAL_CONFIDENCE", "85"))
MIN_REVERSAL_EDGE = float(os.getenv("MIN_REVERSAL_EDGE", "2.5"))
MIN_REVERSAL_PROJECTION_SCORE = int(os.getenv("MIN_REVERSAL_PROJECTION_SCORE", "85"))
MIN_REVERSAL_CONFIRMATION_SCORE = int(os.getenv("MIN_REVERSAL_CONFIRMATION_SCORE", "85"))

# Professional OVER filters.
BLOCK_OVER_ON_THREE_OUTS = os.getenv("BLOCK_OVER_ON_THREE_OUTS", "true").lower() == "true"
BLOCK_OVER_AT_INNING_TRANSITION = os.getenv("BLOCK_OVER_AT_INNING_TRANSITION", "true").lower() == "true"
BLOCK_CONTACT_ONLY_OVER = os.getenv("BLOCK_CONTACT_ONLY_OVER", "true").lower() == "true"
MIN_CONTACT_ONLY_P2R = int(os.getenv("MIN_CONTACT_ONLY_P2R", "75"))
MIN_CONTACT_ONLY_TRAFFIC_CONV = int(os.getenv("MIN_CONTACT_ONLY_TRAFFIC_CONV", "55"))

# Extreme totals need an elite setup, not just a model projection.
MIN_EXTREME_TOTAL_EDGE_V25 = float(os.getenv("MIN_EXTREME_TOTAL_EDGE_V25", "4.5"))
MIN_EXTREME_TOTAL_CONFIRMATION_V25 = int(os.getenv("MIN_EXTREME_TOTAL_CONFIRMATION_V25", "85"))
MIN_EXTREME_TOTAL_PROJECTION_V25 = int(os.getenv("MIN_EXTREME_TOTAL_PROJECTION_V25", "85"))
MIN_EXTREME_TOTAL_P2R_V25 = int(os.getenv("MIN_EXTREME_TOTAL_P2R_V25", "85"))
MIN_EXTREME_TOTAL_CONV_V25 = int(os.getenv("MIN_EXTREME_TOTAL_CONV_V25", "85"))

# Early UNDERs are dangerous unless run suppression is obvious.
MIN_EARLY_UNDER_INNING = int(os.getenv("MIN_EARLY_UNDER_INNING", "5"))
MIN_EARLY_UNDER_K_ENV = int(os.getenv("MIN_EARLY_UNDER_K_ENV", "75"))
MIN_EARLY_UNDER_BULPEN_LOCK = int(os.getenv("MIN_EARLY_UNDER_BULPEN_LOCK", "65"))
MAX_EARLY_UNDER_CONTACT = int(os.getenv("MAX_EARLY_UNDER_CONTACT", "35"))
MAX_EARLY_UNDER_P2R = int(os.getenv("MAX_EARLY_UNDER_P2R", "25"))
MIN_EARLY_UNDER_EDGE = float(os.getenv("MIN_EARLY_UNDER_EDGE", "1.8"))

# V2.6 final decision engine. This is the single SMS gate.
# It prevents duplicate theses, opposite-side confusion, and parser-built SMS.
ENABLE_V26_SINGLE_DECISION_ENGINE = os.getenv("ENABLE_V26_SINGLE_DECISION_ENGINE", "true").lower() == "true"
V26_ONE_BET_NOW_PER_GAME = os.getenv("V26_ONE_BET_NOW_PER_GAME", "true").lower() == "true"
V26_ALLOW_REVERSAL_ONLY = os.getenv("V26_ALLOW_REVERSAL_ONLY", "true").lower() == "true"
V26_MIN_BETNOW_CONFIDENCE = int(os.getenv("V26_MIN_BETNOW_CONFIDENCE", "68"))
V26_MIN_BETNOW_EDGE = float(os.getenv("V26_MIN_BETNOW_EDGE", "1.4"))
V26_MIN_VALUE_SCORE = int(os.getenv("V26_MIN_VALUE_SCORE", "70"))
V26_MAX_RISK_SCORE = int(os.getenv("V26_MAX_RISK_SCORE", "54"))
V26_EXTREME_MAX_RISK_SCORE = int(os.getenv("V26_EXTREME_MAX_RISK_SCORE", "40"))
V26_REJECT_STALE_STATUS = os.getenv("V26_REJECT_STALE_STATUS", "true").lower() == "true"

# V3.0 Market Intelligence layer. Uses the same Odds API response when book-level
# data is available. No extra API calls.
ENABLE_MARKET_INTELLIGENCE = os.getenv("ENABLE_MARKET_INTELLIGENCE", "true").lower() == "true"
REQUIRE_MARKET_CONFIRMATION_WHEN_AVAILABLE = os.getenv("REQUIRE_MARKET_CONFIRMATION_WHEN_AVAILABLE", "true").lower() == "true"
MIN_MARKET_BOOKS_FOR_CONFIRMATION = int(os.getenv("MIN_MARKET_BOOKS_FOR_CONFIRMATION", "2"))
MIN_MARKET_CONFIRMATION_SCORE = int(os.getenv("MIN_MARKET_CONFIRMATION_SCORE", "60"))
MIN_MARKET_CONFIRMATION_EXTREME = int(os.getenv("MIN_MARKET_CONFIRMATION_EXTREME", "75"))
MARKET_VELOCITY_WINDOW_SECONDS = int(os.getenv("MARKET_VELOCITY_WINDOW_SECONDS", "900"))
LINE_VELOCITY_STRONG_MOVE = float(os.getenv("LINE_VELOCITY_STRONG_MOVE", "1.0"))
MARKET_DISAGREEMENT_STRONG = float(os.getenv("MARKET_DISAGREEMENT_STRONG", "1.0"))
PREFERRED_BOOKS = [b.strip().lower() for b in os.getenv("PREFERRED_BOOKS", "betmgm,draftkings,fanduel,caesars,espnbet,bet365,fanatics").split(",") if b.strip()]

# V3.6 practical betting-app controls.
# Use major books for market intelligence, but recommend only the book(s) the user can actually bet.
USER_PLAYABLE_BOOKS = [b.strip().lower() for b in os.getenv("USER_PLAYABLE_BOOKS", "betmgm").split(",") if b.strip()]
MARKET_REFERENCE_BOOKS = [b.strip().lower() for b in os.getenv("MARKET_REFERENCE_BOOKS", "draftkings,fanduel,betmgm,caesars").split(",") if b.strip()]
IGNORE_RECOMMENDATION_BOOKS = [b.strip().lower() for b in os.getenv("IGNORE_RECOMMENDATION_BOOKS", "mybookie,mybookieag,mybookie.ag").split(",") if b.strip()]
REQUIRE_PLAYABLE_BOOK_FOR_STRIKE = os.getenv("REQUIRE_PLAYABLE_BOOK_FOR_STRIKE", "true").lower() == "true"
MARKET_DISCOUNT_OVER_BOOST_MIN = float(os.getenv("MARKET_DISCOUNT_OVER_BOOST_MIN", "1.0"))
MARKET_DISCOUNT_OVER_STRONG = float(os.getenv("MARKET_DISCOUNT_OVER_STRONG", "2.0"))
MARKET_DISCOUNT_OVER_EXTREME = float(os.getenv("MARKET_DISCOUNT_OVER_EXTREME", "3.0"))
WEAK_LINEUP_SCORE_BLOCK = int(os.getenv("WEAK_LINEUP_SCORE_BLOCK", "45"))
WEAK_LINEUP_MIN_P2R = int(os.getenv("WEAK_LINEUP_MIN_P2R", "92"))
WEAK_LINEUP_MIN_CONV = int(os.getenv("WEAK_LINEUP_MIN_CONV", "88"))

# V3.7 Market Reaction Engine:
# SHIFT is not trying to predict baseball in isolation. It is trying to find
# where the betting market has overreacted or underreacted to live events.
ENABLE_MARKET_REACTION_ENGINE = os.getenv("ENABLE_MARKET_REACTION_ENGINE", "true").lower() == "true"
INFLATED_TOTAL_MOVE_RUNS = float(os.getenv("INFLATED_TOTAL_MOVE_RUNS", "2.0"))
DISCOUNTED_TOTAL_MOVE_RUNS = float(os.getenv("DISCOUNTED_TOTAL_MOVE_RUNS", "1.5"))
SETTLE_DOWN_MIN_SCORE = int(os.getenv("SETTLE_DOWN_MIN_SCORE", "60"))
CONTINUATION_OVER_MIN_SCORE = int(os.getenv("CONTINUATION_OVER_MIN_SCORE", "65"))
CONTINUATION_MAX_FOR_FADE = int(os.getenv("CONTINUATION_MAX_FOR_FADE", "65"))
FALSE_INFLATION_MIN_SCORE = int(os.getenv("FALSE_INFLATION_MIN_SCORE", "50"))
MARKET_REACTION_MAX_PROJECTION_ADJ = float(os.getenv("MARKET_REACTION_MAX_PROJECTION_ADJ", "2.5"))
INFLATED_UNDER_MIN_INNING = int(os.getenv("INFLATED_UNDER_MIN_INNING", "3"))
INFLATED_UNDER_MIN_EDGE = float(os.getenv("INFLATED_UNDER_MIN_EDGE", "0.75"))
INFLATED_UNDER_ALLOW_CURRENT_PRESSURE = int(os.getenv("INFLATED_UNDER_ALLOW_CURRENT_PRESSURE", "65"))
INFLATED_UNDER_ALLOW_CONTACT = int(os.getenv("INFLATED_UNDER_ALLOW_CONTACT", "68"))
INFLATED_UNDER_MAX_TRAFFIC = int(os.getenv("INFLATED_UNDER_MAX_TRAFFIC", "75"))
INFLATED_UNDER_MAX_P2R_SOFT = int(os.getenv("INFLATED_UNDER_MAX_P2R_SOFT", "88"))
INFLATED_OVER_REQUIRE_CONTINUATION = os.getenv("INFLATED_OVER_REQUIRE_CONTINUATION", "true").lower() == "true"
INFLATED_OVER_MIN_CONTINUATION_EDGE = float(os.getenv("INFLATED_OVER_MIN_CONTINUATION_EDGE", "2.0"))
INFLATED_OVER_MIN_P2R = int(os.getenv("INFLATED_OVER_MIN_P2R", "82"))
INFLATED_OVER_MIN_CONV = int(os.getenv("INFLATED_OVER_MIN_CONV", "75"))
MARKET_REACTION_MIN_SCORE_GAP = int(os.getenv("MARKET_REACTION_MIN_SCORE_GAP", "5"))

# V3.7.2 Pitching Dominance UNDER:
# Early low-total games where both the market and game state point toward true run suppression.
ENABLE_PITCHING_DOMINANCE_UNDER = os.getenv("ENABLE_PITCHING_DOMINANCE_UNDER", "true").lower() == "true"
PITCHING_DOMINANCE_OPEN_MAX = float(os.getenv("PITCHING_DOMINANCE_OPEN_MAX", "8.5"))
PITCHING_DOMINANCE_MIN_INNING = int(os.getenv("PITCHING_DOMINANCE_MIN_INNING", "2"))
PITCHING_DOMINANCE_MAX_INNING = int(os.getenv("PITCHING_DOMINANCE_MAX_INNING", "5"))
PITCHING_DOMINANCE_MAX_LIVE_DROP = float(os.getenv("PITCHING_DOMINANCE_MAX_LIVE_DROP", "1.5"))
PITCHING_DOMINANCE_MIN_SCORE = int(os.getenv("PITCHING_DOMINANCE_MIN_SCORE", "68"))
PITCHING_DOMINANCE_MIN_EDGE = float(os.getenv("PITCHING_DOMINANCE_MIN_EDGE", "0.35"))
PITCHING_DOMINANCE_MAX_RUNS = int(os.getenv("PITCHING_DOMINANCE_MAX_RUNS", "2"))
PITCHING_DOMINANCE_MAX_RECENT_BASERUNNERS = int(os.getenv("PITCHING_DOMINANCE_MAX_RECENT_BASERUNNERS", "2"))
PITCHING_DOMINANCE_MAX_CONTACT = int(os.getenv("PITCHING_DOMINANCE_MAX_CONTACT", "45"))
PITCHING_DOMINANCE_MAX_STRESS = int(os.getenv("PITCHING_DOMINANCE_MAX_STRESS", "55"))
PITCHING_DOMINANCE_MIN_K_ENV = int(os.getenv("PITCHING_DOMINANCE_MIN_K_ENV", "65"))
PITCHING_DOMINANCE_MIN_BPLOCK = int(os.getenv("PITCHING_DOMINANCE_MIN_BPLOCK", "52"))

# V3.1 practical live-app controls.
# Do not chase markets that already moved too far unless baseball + market confirmation are elite.
DO_NOT_CHASE_MOVE_FROM_OPEN = float(os.getenv("DO_NOT_CHASE_MOVE_FROM_OPEN", "3.0"))
DO_NOT_CHASE_MIN_CONFIDENCE = int(os.getenv("DO_NOT_CHASE_MIN_CONFIDENCE", "88"))
DO_NOT_CHASE_MIN_EDGE = float(os.getenv("DO_NOT_CHASE_MIN_EDGE", "4.0"))
DO_NOT_CHASE_MIN_MARKET_CONFIRMATION = int(os.getenv("DO_NOT_CHASE_MIN_MARKET_CONFIRMATION", "78"))
PRICE_ADJUSTED_HALF_RUN_VALUE = int(os.getenv("PRICE_ADJUSTED_HALF_RUN_VALUE", "18"))

# V3.2 practical live-app controls.
# These force the final recommended play to be what a user can realistically bet.
ENABLE_V32_BEST_LINE_REWRITE = os.getenv("ENABLE_V32_BEST_LINE_REWRITE", "true").lower() == "true"
MAX_BEST_LINE_AGE_SECONDS = int(os.getenv("MAX_BEST_LINE_AGE_SECONDS", "90"))
REQUIRE_POSITIVE_EV = os.getenv("REQUIRE_POSITIVE_EV", "true").lower() == "true"
MIN_EXPECTED_VALUE = float(os.getenv("MIN_EXPECTED_VALUE", "0.00"))
RUN_EDGE_TO_PROB_PER_RUN = float(os.getenv("RUN_EDGE_TO_PROB_PER_RUN", "0.055"))
MIN_STRONG_PATTERN_SAMPLE = int(os.getenv("MIN_STRONG_PATTERN_SAMPLE", "30"))
MIN_AUTO_ADJUST_SAMPLE = int(os.getenv("MIN_AUTO_ADJUST_SAMPLE", "75"))
APP_STATUS_STALE_SECONDS = int(os.getenv("APP_STATUS_STALE_SECONDS", "90"))

# V3.3 ROI discipline controls. Fewer bad bets > more alerts.
ENABLE_V33_ROI_DISCIPLINE = os.getenv("ENABLE_V33_ROI_DISCIPLINE", "true").lower() == "true"
MARKET_FIRST_MIN_BOOKS = int(os.getenv("MARKET_FIRST_MIN_BOOKS", "2"))
MARKET_FIRST_MIN_CONFIRMATION = int(os.getenv("MARKET_FIRST_MIN_CONFIRMATION", "62"))
MARKET_FIRST_MIN_EV = float(os.getenv("MARKET_FIRST_MIN_EV", "0.015"))
MAX_ENTRY_HALF_RUN_BUFFER = float(os.getenv("MAX_ENTRY_HALF_RUN_BUFFER", "0.5"))
MAX_ENTRY_PRICE_OVER = int(os.getenv("MAX_ENTRY_PRICE_OVER", "-135"))
MAX_ENTRY_PRICE_UNDER = int(os.getenv("MAX_ENTRY_PRICE_UNDER", "-130"))
EARLY_OVER_MIN_INNING = int(os.getenv("EARLY_OVER_MIN_INNING", "3"))
EARLY_OVER_MIN_P2R = int(os.getenv("EARLY_OVER_MIN_P2R", "82"))
EARLY_OVER_MIN_TRAFFIC = int(os.getenv("EARLY_OVER_MIN_TRAFFIC", "65"))
EARLY_OVER_MIN_STRESS = int(os.getenv("EARLY_OVER_MIN_STRESS", "75"))
LATE_OVER_MAX_INNING = int(os.getenv("LATE_OVER_MAX_INNING", "7"))
LATE_OVER_MIN_EDGE = float(os.getenv("LATE_OVER_MIN_EDGE", "3.0"))
LATE_OVER_MIN_CONFIRMATION = int(os.getenv("LATE_OVER_MIN_CONFIRMATION", "88"))
UNDER_DEFAULT_MIN_INNING_V33 = int(os.getenv("UNDER_DEFAULT_MIN_INNING_V33", "5"))
UNDER_MIN_RUN_PREVENTION_V33 = int(os.getenv("UNDER_MIN_RUN_PREVENTION_V33", "72"))
UNDER_MIN_KENV_V33 = int(os.getenv("UNDER_MIN_KENV_V33", "62"))
UNDER_MIN_BPLOCK_V33 = int(os.getenv("UNDER_MIN_BPLOCK_V33", "55"))
GOOD_BET_CLV_THRESHOLD = float(os.getenv("GOOD_BET_CLV_THRESHOLD", "0.5"))

# V3.4 intelligence controls. These are designed to improve bet selection without
# adding API calls. Where premium data is unavailable, they degrade safely to neutral.
ENABLE_V34_INTELLIGENCE = os.getenv("ENABLE_V34_INTELLIGENCE", "true").lower() == "true"
LINEUP_POCKET_MIN_OVER = int(os.getenv("LINEUP_POCKET_MIN_OVER", "58"))
LINEUP_POCKET_MAX_UNDER_RISK = int(os.getenv("LINEUP_POCKET_MAX_UNDER_RISK", "70"))
BULLPEN_CONTEXT_MIN_OVER = int(os.getenv("BULLPEN_CONTEXT_MIN_OVER", "48"))
BULLPEN_CONTEXT_MIN_UNDER = int(os.getenv("BULLPEN_CONTEXT_MIN_UNDER", "58"))
CONFIDENCE_DECAY_BLOCK_LEVEL = int(os.getenv("CONFIDENCE_DECAY_BLOCK_LEVEL", "35"))
CONFIDENCE_DECAY_WARNING_LEVEL = int(os.getenv("CONFIDENCE_DECAY_WARNING_LEVEL", "20"))
ENABLE_HISTORICAL_EV_CALIBRATION = os.getenv("ENABLE_HISTORICAL_EV_CALIBRATION", "true").lower() == "true"
MIN_EV_CALIBRATION_SAMPLE = int(os.getenv("MIN_EV_CALIBRATION_SAMPLE", "50"))
EV_CALIBRATION_CACHE_SECONDS = int(os.getenv("EV_CALIBRATION_CACHE_SECONDS", "300"))
PREFERRED_LEADING_BOOK_BONUS = int(os.getenv("PREFERRED_LEADING_BOOK_BONUS", "6"))




# SMS safety:
# Twilio rejects message bodies over 1600 characters.
# Keep texts short; keep full details in Railway logs.
MAX_SMS_CHARS = int(os.getenv("MAX_SMS_CHARS", "1350"))

# V2.2.5 SMS behavior:
# WATCH alerts remain in Railway logs only.
# Only STRIKE / BET NOW alerts are sent by SMS.
SEND_ONLY_STRIKE_SMS = os.getenv("SEND_ONLY_STRIKE_SMS", "true").lower() == "true"
SHORT_STRIKE_SMS = os.getenv("SHORT_STRIKE_SMS", "true").lower() == "true"
MAX_SHORT_SMS_CHARS = int(os.getenv("MAX_SHORT_SMS_CHARS", "650"))
ELITE_STRIKE_PROJECTION = int(os.getenv("ELITE_STRIKE_PROJECTION", "85"))
ELITE_STRIKE_CONFIRMATION = int(os.getenv("ELITE_STRIKE_CONFIRMATION", "80"))
ELITE_STRIKE_EDGE = float(os.getenv("ELITE_STRIKE_EDGE", "2.0"))

# V2.3.0 professional calibration:
# Improve accuracy without handcuffing the model or increasing API credit usage.
ENABLE_TIME_REMAINING_ADJUSTMENT = os.getenv("ENABLE_TIME_REMAINING_ADJUSTMENT", "true").lower() == "true"
ENABLE_DUPLICATE_STRIKE_LOCK = os.getenv("ENABLE_DUPLICATE_STRIKE_LOCK", "true").lower() == "true"
ENABLE_STRIKE_HISTORY = os.getenv("ENABLE_STRIKE_HISTORY", "true").lower() == "true"
ENABLE_CLV_TRACKING = os.getenv("ENABLE_CLV_TRACKING", "true").lower() == "true"

# Re-alert only when the same game/side materially improves.
RE_ALERT_MIN_EDGE_IMPROVEMENT = float(os.getenv("RE_ALERT_MIN_EDGE_IMPROVEMENT", "2.0"))
RE_ALERT_MIN_PROJECTION_IMPROVEMENT = float(os.getenv("RE_ALERT_MIN_PROJECTION_IMPROVEMENT", "2.5"))
RE_ALERT_MIN_CONFIRMATION_IMPROVEMENT = int(os.getenv("RE_ALERT_MIN_CONFIRMATION_IMPROVEMENT", "15"))

# Time remaining adjustment is intentionally moderate so we do not kill good winners.
TIME_ADJ_INNING_1_4 = float(os.getenv("TIME_ADJ_INNING_1_4", "1.00"))
TIME_ADJ_INNING_5_6 = float(os.getenv("TIME_ADJ_INNING_5_6", "0.90"))
TIME_ADJ_INNING_7_8 = float(os.getenv("TIME_ADJ_INNING_7_8", "0.78"))
TIME_ADJ_INNING_9_PLUS = float(os.getenv("TIME_ADJ_INNING_9_PLUS", "0.55"))

# Credit usage:
# These features do not add extra API calls. They only use data already fetched during normal polling.

# V2.3.1 Winner Pattern Enhancements:
# These are additive/refinement signals, not restrictive filters.
# They use already-fetched MLB feed + odds data only.
ENABLE_WINNER_PATTERN_ENHANCEMENTS = os.getenv("ENABLE_WINNER_PATTERN_ENHANCEMENTS", "true").lower() == "true"
P2R_ELITE_THRESHOLD = int(os.getenv("P2R_ELITE_THRESHOLD", "85"))
P2R_SUPER_ELITE_THRESHOLD = int(os.getenv("P2R_SUPER_ELITE_THRESHOLD", "95"))
P2R_ELITE_BOOST = int(os.getenv("P2R_ELITE_BOOST", "8"))
P2R_SUPER_ELITE_BOOST = int(os.getenv("P2R_SUPER_ELITE_BOOST", "14"))
MARKET_LAG_STRONG = float(os.getenv("MARKET_LAG_STRONG", "2.0"))
MARKET_LAG_ELITE = float(os.getenv("MARKET_LAG_ELITE", "4.0"))
CONV_ACCEL_MIN_JUMP = int(os.getenv("CONV_ACCEL_MIN_JUMP", "15"))
SIGNAL_STACK_STRONG = int(os.getenv("SIGNAL_STACK_STRONG", "4"))
SIGNAL_STACK_ELITE = int(os.getenv("SIGNAL_STACK_ELITE", "5"))

# V2.4.0 professional refinement layer from alert reviews:
# Adds the profiles that mattered in our Mets/Mariners review:
# bullpen lockdown, strikeout environment, traffic conversion, and hard-hit efficiency.
ENABLE_PRO_REFINEMENT_LAYER = os.getenv("ENABLE_PRO_REFINEMENT_LAYER", "true").lower() == "true"
MIN_K_ENV_UNDER_STRIKE = int(os.getenv("MIN_K_ENV_UNDER_STRIKE", "62"))
MIN_BULLPEN_LOCKDOWN_UNDER_STRIKE = int(os.getenv("MIN_BULLPEN_LOCKDOWN_UNDER_STRIKE", "55"))
MAX_EXTREME_TOTAL_STRIKE_LINE = float(os.getenv("MAX_EXTREME_TOTAL_STRIKE_LINE", "11.5"))
MIN_EXTREME_TOTAL_CONFIRMATION = int(os.getenv("MIN_EXTREME_TOTAL_CONFIRMATION", "82"))
MIN_EXTREME_TOTAL_PROJECTION = int(os.getenv("MIN_EXTREME_TOTAL_PROJECTION", "82"))
MIN_EXTREME_TOTAL_EDGE = float(os.getenv("MIN_EXTREME_TOTAL_EDGE", "2.0"))


# V2.2.4 decision calibration:
# Separates projected edge from live confirmation so WATCH/STRIKE is cleaner.
MIN_OVER_CONFIRMATION_FOR_STRIKE = int(os.getenv("MIN_OVER_CONFIRMATION_FOR_STRIKE", "60"))
MIN_LATE_OVER_CONFIRMATION_FOR_STRIKE = int(os.getenv("MIN_LATE_OVER_CONFIRMATION_FOR_STRIKE", "68"))
MIN_UNDER_CONFIRMATION_FOR_STRIKE = int(os.getenv("MIN_UNDER_CONFIRMATION_FOR_STRIKE", "62"))
MIN_LATE_UNDER_CONFIRMATION_FOR_STRIKE = int(os.getenv("MIN_LATE_UNDER_CONFIRMATION_FOR_STRIKE", "68"))
MIN_UNDER_STRIKE_EDGE_RUNS = float(os.getenv("MIN_UNDER_STRIKE_EDGE_RUNS", "1.0"))
MIN_LATE_OVER_STRIKE_EDGE_RUNS = float(os.getenv("MIN_LATE_OVER_STRIKE_EDGE_RUNS", "1.25"))
MAX_PROJECTION_EDGE_INNING_7 = float(os.getenv("MAX_PROJECTION_EDGE_INNING_7", "4.5"))
MAX_PROJECTION_EDGE_INNING_8 = float(os.getenv("MAX_PROJECTION_EDGE_INNING_8", "3.0"))
MAX_PROJECTION_EDGE_INNING_9 = float(os.getenv("MAX_PROJECTION_EDGE_INNING_9", "2.0"))

# Professional decision-layer gates
MIN_STRIKE_CONFIDENCE = int(os.getenv("MIN_STRIKE_CONFIDENCE", "64"))
MIN_WATCH_CONFIDENCE = int(os.getenv("MIN_WATCH_CONFIDENCE", "52"))
MAX_LATE_OVER_INNING = int(os.getenv("MAX_LATE_OVER_INNING", "8"))

# V2.2 predictive layers
MIN_OVER_RUN_CONVERSION_FOR_STRIKE = int(os.getenv("MIN_OVER_RUN_CONVERSION_FOR_STRIKE", "62"))
MIN_UNDER_RUN_PREVENTION_FOR_STRIKE = int(os.getenv("MIN_UNDER_RUN_PREVENTION_FOR_STRIKE", "66"))
MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH = int(os.getenv("MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH", "62"))
MIN_PREDICTIVE_MARKET_MOVE_FOR_STRIKE = int(os.getenv("MIN_PREDICTIVE_MARKET_MOVE_FOR_STRIKE", "76"))
MARKET_HAS_ALREADY_MOVED_RUNS = float(os.getenv("MARKET_HAS_ALREADY_MOVED_RUNS", "1.5"))
BEST_ENTRY_HALF_RUN_BUFFER = float(os.getenv("BEST_ENTRY_HALF_RUN_BUFFER", "0.5"))

# V2.2.1 professional over-prediction layer
ENABLE_PRE_RUN_OVER_WATCH = os.getenv("ENABLE_PRE_RUN_OVER_WATCH", "true").lower() == "true"
PRE_RUN_OVER_WATCH_SCORE = int(os.getenv("PRE_RUN_OVER_WATCH_SCORE", "58"))
PRE_RUN_OVER_STRIKE_SCORE = int(os.getenv("PRE_RUN_OVER_STRIKE_SCORE", "72"))
MIN_OVER_RUN_CONVERSION_FOR_WATCH = int(os.getenv("MIN_OVER_RUN_CONVERSION_FOR_WATCH", "48"))
MARKET_LAG_MAX_UPWARD_MOVE = float(os.getenv("MARKET_LAG_MAX_UPWARD_MOVE", "1.0"))
PRE_RUN_MAX_FAKE_PRESSURE = int(os.getenv("PRE_RUN_MAX_FAKE_PRESSURE", "55"))

# Live-evidence gates:
# Prevents false pregame / first-pitch alerts when there is no real game data yet.
MIN_LIVE_PITCHES_FOR_STRIKE = int(os.getenv("MIN_LIVE_PITCHES_FOR_STRIKE", "18"))
MIN_BALLS_IN_PLAY_FOR_STRIKE = int(os.getenv("MIN_BALLS_IN_PLAY_FOR_STRIKE", "2"))
MIN_REAL_SIGNAL_COUNT = int(os.getenv("MIN_REAL_SIGNAL_COUNT", "2"))
MIN_INNING_FOR_NEUTRAL_ALERT = int(os.getenv("MIN_INNING_FOR_NEUTRAL_ALERT", "99"))

ODDS_MARKETS = os.getenv("ODDS_MARKETS", "totals")

TEAM_MAP = {
    "Oakland Athletics": "Athletics",
    "Athletics": "Athletics",
    "Arizona Diamondbacks": "Diamondbacks",
    "Atlanta Braves": "Braves",
    "Baltimore Orioles": "Orioles",
    "Boston Red Sox": "Red Sox",
    "Chicago Cubs": "Cubs",
    "Chicago White Sox": "White Sox",
    "Cincinnati Reds": "Reds",
    "Cleveland Guardians": "Guardians",
    "Colorado Rockies": "Rockies",
    "Detroit Tigers": "Tigers",
    "Houston Astros": "Astros",
    "Kansas City Royals": "Royals",
    "Los Angeles Angels": "Angels",
    "Los Angeles Dodgers": "Dodgers",
    "Miami Marlins": "Marlins",
    "Milwaukee Brewers": "Brewers",
    "Minnesota Twins": "Twins",
    "New York Mets": "Mets",
    "New York Yankees": "Yankees",
    "Philadelphia Phillies": "Phillies",
    "Pittsburgh Pirates": "Pirates",
    "San Diego Padres": "Padres",
    "San Francisco Giants": "Giants",
    "Seattle Mariners": "Mariners",
    "St. Louis Cardinals": "Cardinals",
    "Tampa Bay Rays": "Rays",
    "Texas Rangers": "Rangers",
    "Toronto Blue Jays": "Blue Jays",
    "Washington Nationals": "Nationals",
}


FINAL_STATUS_WORDS = {
    "final",
    "game over",
    "completed early",
    "completed",
    "cancelled",
    "canceled",
    "postponed",
    "suspended",
}


def normalize_status(status):
    return str(status or "").strip().lower()


def is_final_status(status):
    """
    Current-day final lock.
    Once a game is final/completed/postponed/suspended/cancelled today,
    stop tracking it for the rest of the day.
    State resets automatically on a new calendar day through load_state().
    """
    s = normalize_status(status)
    if not s:
        return False
    return s in FINAL_STATUS_WORDS or s.startswith("final")


def schedule_status(game):
    status = game.get("status", {}) or {}
    return (
        status.get("abstractGameState")
        or status.get("detailedState")
        or status.get("codedGameState")
        or ""
    )


def schedule_label(game):
    home = game.get("teams", {}).get("home", {}).get("team", {}).get("name", "Home")
    away = game.get("teams", {}).get("away", {}).get("team", {}).get("name", "Away")
    return f"{away} at {home}"


def is_final_locked_today(state, game_pk):
    """
    State file resets daily. If a game is marked final today, skip it completely.
    """
    return bool(state.setdefault("final_games", {}).get(str(game_pk)))


def mark_final_locked_today(state, game_pk, label="", status="", score=""):
    state.setdefault("final_games", {})[str(game_pk)] = {
        "date": today(),
        "label": label,
        "status": status,
        "score": score,
        "locked_at": now_local().isoformat(),
    }
    games = state.setdefault("games", {})
    games.setdefault(str(game_pk), {})
    games[str(game_pk)]["final_locked"] = True
    games[str(game_pk)]["final_status"] = status
    games[str(game_pk)]["final_score"] = score



def now_local():
    return datetime.now(TZ)


def today():
    return now_local().strftime("%Y-%m-%d")


def clean_team(name):
    if not name:
        return ""
    return TEAM_MAP.get(name, name).lower().replace(".", "").replace("-", " ").strip()


def clamp(x, lo=0, hi=100):
    return max(lo, min(hi, x))


def avg(nums):
    return round(sum(nums) / len(nums), 2) if nums else 0


def safe_float(x, default=0):
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def safe_int(x, default=0):
    try:
        if x is None:
            return default
        return int(x)
    except Exception:
        return default


def price_ok(price, edge):
    if price is None:
        return True
    p = int(price)
    if p < MAX_PRICE_FAVORITE or p > MAX_PRICE_DOG:
        return False
    if p <= -135 and edge < 1.35:
        return False
    if p <= -125 and edge < 1.15:
        return False
    return True


def market_label(price):
    if price is None:
        return "Unknown price"
    p = int(price)
    if -115 <= p <= 100:
        return "Clean price"
    if -140 <= p < -115 or 100 < p <= 110:
        return "Acceptable price"
    return "Bad price"


# -----------------------------
# V3.6 practical book / market discount helpers
# -----------------------------

def normalize_book_name(name):
    return str(name or "").lower().replace(" ", "").replace("_", "").replace("-", "").replace(".", "").strip()


def book_matches(book, names):
    b = normalize_book_name(book)
    normalized = [normalize_book_name(n) for n in (names or [])]
    return any(n and (b == n or n in b or b in n) for n in normalized)


def is_user_playable_book(book):
    return book_matches(book, USER_PLAYABLE_BOOKS)


def is_ignored_recommendation_book(book):
    return book_matches(book, IGNORE_RECOMMENDATION_BOOKS)


def is_market_reference_book(book):
    """
    V3.6.2: true-market math should use major public books only.
    Ignored/offshore books are never allowed to influence consensus, velocity,
    best line, EV, or recommendation text.
    """
    if is_ignored_recommendation_book(book):
        return False
    if MARKET_REFERENCE_BOOKS:
        return book_matches(book, MARKET_REFERENCE_BOOKS)
    return True


def remove_ignored_book_totals(book_totals):
    """Remove books like MyBookie from all market math and app logic."""
    return [b for b in (book_totals or []) if not is_ignored_recommendation_book(b.get("book"))]


def playable_book_filter_candidates(candidates):
    """Return candidates from books the user can actually bet."""
    if not candidates:
        return []
    clean = [c for c in candidates if not is_ignored_recommendation_book(c.get("book"))]
    if USER_PLAYABLE_BOOKS:
        return [c for c in clean if is_user_playable_book(c.get("book"))]
    return clean


def market_reference_book_totals(book_totals):
    """
    Major-market subset for confirmation.
    V3.6.2 never falls back to ignored/offshore books. If configured major
    books are unavailable, fall back only to non-ignored books.
    """
    clean = remove_ignored_book_totals(book_totals)
    if not clean:
        return []
    if not MARKET_REFERENCE_BOOKS:
        return clean
    refs = [b for b in clean if is_market_reference_book(b.get("book"))]
    return refs or clean


def market_discount_value(first_seen_total, live_total):
    fs = safe_float(first_seen_total, None)
    live = safe_float(live_total, None)
    if fs is None or live is None:
        return 0.0
    return round(fs - live, 2)


def market_discount_score(side, first_seen_total, live_total, scores=None):
    """Positive score means the live number is discounted in the direction we want."""
    side = str(side or "").upper()
    discount = market_discount_value(first_seen_total, live_total)
    if side == "OVER":
        d = discount
    elif side == "UNDER":
        d = -discount
    else:
        return 0
    if d < MARKET_DISCOUNT_OVER_BOOST_MIN:
        return 0
    score = 30
    if d >= MARKET_DISCOUNT_OVER_STRONG:
        score += 25
    if d >= MARKET_DISCOUNT_OVER_EXTREME:
        score += 25
    p2r = safe_int((scores or {}).get("pressure_to_runs"), 0)
    conv = safe_int((scores or {}).get("run_conversion"), safe_int((scores or {}).get("traffic_conversion"), 0))
    if side == "OVER" and p2r >= 85 and conv >= 80:
        score += 20
    return round(clamp(score))


# -----------------------------
# V3.7 Market Reaction Engine
# -----------------------------

def market_reaction_move(opening_total, first_seen_total, live_total):
    """Use first_seen when true opening is unknown. Positive = market inflated upward."""
    live = safe_float(live_total, None)
    ref = safe_float(opening_total, None)
    if ref is None:
        ref = safe_float(first_seen_total, None)
    if live is None or ref is None:
        return 0.0, ref
    return round(live - ref, 2), ref


def market_reaction_scores(info, opening_total, first_seen_total, live_total, scores):
    """
    Core V3.7 scores:
    - Settle Down Score: market may have overreacted upward; possible inflated UNDER.
    - Continuation Score: run environment is still alive; do not blindly fade.
    - Market Reaction Profile: Discounted OVER / Inflated UNDER / Continuation OVER / False Inflation Fade.
    """
    scores = scores or {}
    move, ref = market_reaction_move(opening_total, first_seen_total, live_total)
    inning = safe_int((info or {}).get("inning"), 1)
    innings_left = innings_remaining_estimate(info or {})
    total_runs = safe_int((info or {}).get("total_runs"), 0)
    current_pressure = safe_int(scores.get("current_inning_pressure"), 0)
    remaining_opp = safe_int(scores.get("remaining_opportunity"), 0)
    stress = safe_int(scores.get("pitcher_stress"), 0)
    p2r = safe_int(scores.get("pressure_to_runs"), 0)
    conv = safe_int(scores.get("run_conversion"), 0)
    traffic = safe_int(scores.get("traffic_conversion"), 0)
    contact = safe_int(scores.get("contact_quality"), 0)
    contact_trend = safe_int(scores.get("contact_trend"), 50)
    hard_hit = safe_int(scores.get("hard_hit_efficiency"), 0)
    fake = safe_int(scores.get("fake_pressure"), 0)
    run_prev = safe_int(scores.get("run_prevention"), 0)
    under_env = safe_int(scores.get("under_environment"), 0)
    k_env = safe_int(scores.get("strikeout_environment"), 0)
    bp_lock = safe_int(scores.get("bullpen_lockdown"), 0)
    bullpen_risk = safe_int(scores.get("bullpen_risk"), 50)
    blowout = safe_int(scores.get("blowout_kill"), 0)

    # Continuation means the run environment may keep expanding beyond the new number.
    continuation = 0
    continuation += max(0, move) * 8
    continuation += current_pressure * 0.18
    continuation += remaining_opp * 0.12
    continuation += stress * 0.13
    continuation += p2r * 0.18
    continuation += conv * 0.12
    continuation += traffic * 0.11
    continuation += contact * 0.10
    continuation += max(0, contact_trend - 50) * 0.20
    continuation += hard_hit * 0.09
    continuation += bullpen_risk * 0.08
    continuation -= run_prev * 0.08
    continuation -= k_env * 0.06
    continuation -= bp_lock * 0.06
    continuation -= blowout * 0.10

    # Settle down means the market likely priced in runs already scored more than future pressure.
    settle = 0
    settle += max(0, move) * 13
    settle += max(0, total_runs - 3) * 4
    settle += max(0, 65 - current_pressure) * 0.22
    settle += max(0, 65 - remaining_opp) * 0.14
    settle += max(0, 65 - contact) * 0.16
    settle += max(0, 65 - traffic) * 0.12
    settle += max(0, 65 - hard_hit) * 0.10
    settle += fake * 0.16
    settle += run_prev * 0.18
    settle += under_env * 0.18
    settle += k_env * 0.12
    settle += bp_lock * 0.12
    settle += blowout * 0.16
    if inning >= INFLATED_UNDER_MIN_INNING:
        settle += 6
    if innings_left <= 3.5:
        settle += 8
    if innings_left <= 2.0:
        settle += 8
    if p2r >= 85 or conv >= 85 or traffic >= 70:
        settle -= 12
    if stress >= 85 and p2r >= 85:
        settle -= 10

    false_inflation = 0
    false_inflation += max(0, move) * 12
    false_inflation += fake * 0.22
    false_inflation += run_prev * 0.18
    false_inflation += under_env * 0.16
    false_inflation += max(0, 60 - current_pressure) * 0.14
    false_inflation += max(0, 60 - contact) * 0.12
    false_inflation -= max(0, p2r - 70) * 0.18
    false_inflation -= max(0, conv - 70) * 0.14

    discounted_over = 0
    discounted_over += max(0, -move) * 14
    discounted_over += p2r * 0.18
    discounted_over += conv * 0.15
    discounted_over += stress * 0.12
    discounted_over += traffic * 0.12
    discounted_over += hard_hit * 0.08
    discounted_over -= run_prev * 0.08
    discounted_over -= bp_lock * 0.06

    # V3.7.8 Continuation Exhaustion Score:
    # Higher = the market may have already priced in the continuation.
    # This does NOT kill continuation overs; it forces elite proof when the
    # live total has already climbed sharply.
    continuation_exhaustion = 0
    continuation_exhaustion += max(0, move - 2.0) * 18
    if move >= CONT_EXHAUSTION_MOVE_CAUTION:
        continuation_exhaustion += 12
    if move >= CONT_EXHAUSTION_MOVE_DANGER:
        continuation_exhaustion += 18
    if inning >= 5:
        continuation_exhaustion += 8
    if inning >= 6:
        continuation_exhaustion += 8
    if innings_left <= 4.0:
        continuation_exhaustion += 8
    if innings_left <= 3.0:
        continuation_exhaustion += 10
    if total_runs >= 7:
        continuation_exhaustion += 6
    if total_runs >= 10:
        continuation_exhaustion += 8
    # Elite live baseball evidence reduces exhaustion risk.
    if p2r >= 95 and conv >= 90 and current_pressure >= 70:
        continuation_exhaustion -= 22
    elif p2r >= 88 and conv >= 82 and current_pressure >= 55:
        continuation_exhaustion -= 12
    if traffic >= 75:
        continuation_exhaustion -= 6
    if hard_hit >= 75 or contact >= 75:
        continuation_exhaustion -= 6
    if blowout >= 70:
        continuation_exhaustion += 10

    if move >= INFLATED_TOTAL_MOVE_RUNS:
        if settle >= SETTLE_DOWN_MIN_SCORE and continuation <= CONTINUATION_MAX_FOR_FADE:
            profile = "INFLATED_UNDER"
        elif false_inflation >= FALSE_INFLATION_MIN_SCORE and continuation < CONTINUATION_OVER_MIN_SCORE:
            profile = "FALSE_INFLATION_FADE"
        elif continuation >= CONTINUATION_OVER_MIN_SCORE:
            profile = "CONTINUATION_OVER"
        else:
            profile = "INFLATED_NO_BET"
    elif move <= -DISCOUNTED_TOTAL_MOVE_RUNS:
        profile = "DISCOUNTED_OVER" if discounted_over >= 55 else "DISCOUNTED_NO_BET"
    else:
        profile = "NEUTRAL_MARKET"

    return {
        "market_reaction_move": round(move, 2),
        "market_reaction_reference": ref,
        "settle_down_score": round(clamp(settle)),
        "continuation_score": round(clamp(continuation)),
        "false_inflation_score": round(clamp(false_inflation)),
        "discounted_over_score": round(clamp(discounted_over)),
        "continuation_exhaustion_score": round(clamp(continuation_exhaustion)),
        "market_reaction_profile": profile,
    }


def apply_market_reaction_scores(info, opening_total, first_seen_total, live_total, scores):
    if not ENABLE_MARKET_REACTION_ENGINE:
        return scores or {}
    scores = dict(scores or {})
    scores.update(market_reaction_scores(info, opening_total, first_seen_total, live_total, scores))
    return scores


def market_reaction_projection_adjustment(info, live_total, projected_total, scores):
    """
    V3.7.1: projection adjustment is intentionally conservative. It should help
    expose market-reaction edges, not manufacture them. A side must have a real
    score gap before the projection is moved.
    """
    if not ENABLE_MARKET_REACTION_ENGINE:
        return projected_total
    if live_total is None or projected_total is None:
        return projected_total

    profile = scores.get("market_reaction_profile", "")
    move = safe_float(scores.get("market_reaction_move"), 0)
    settle = safe_int(scores.get("settle_down_score"), 0)
    cont = safe_int(scores.get("continuation_score"), 0)
    disc = safe_int(scores.get("discounted_over_score"), 0)

    adj = 0.0
    if profile in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"] and (settle - cont) >= MARKET_REACTION_MIN_SCORE_GAP:
        adj = -min(MARKET_REACTION_MAX_PROJECTION_ADJ, max(0.4, move * 0.35 + (settle - cont) * 0.012))
    elif profile == "CONTINUATION_OVER" and (cont - settle) >= MARKET_REACTION_MIN_SCORE_GAP:
        adj = min(1.10, max(0.25, move * 0.12 + (cont - settle) * 0.008))
    elif profile == "DISCOUNTED_OVER" and disc >= 55:
        adj = min(MARKET_REACTION_MAX_PROJECTION_ADJ, max(0.35, abs(move) * 0.35 + disc * 0.008))
    elif profile == "PITCHING_DOMINANCE_UNDER":
        pd = safe_int(scores.get("pitching_dominance_under_score"), 0)
        adj = -min(1.75, max(0.35, (pd - 55) * 0.025))

    scores["market_reaction_projection_adjustment"] = round(adj, 2)
    return round(projected_total + adj, 1)


def market_reaction_scenario_label(profile):
    labels = {
        "DISCOUNTED_OVER": "Market Underreaction → Discounted OVER",
        "INFLATED_UNDER": "Market Overreaction → Inflated UNDER",
        "FALSE_INFLATION_FADE": "False Inflation Fade → UNDER",
        "CONTINUATION_OVER": "True Run Environment → Continuation OVER",
        "PITCHING_DOMINANCE_UNDER": "Pitching Dominance → Early UNDER",
        "INFLATED_NO_BET": "Inflated Market → No Bet Unless Clear",
        "DISCOUNTED_NO_BET": "Discounted Market → No Bet Unless Pressure Confirms",
        "NEUTRAL_MARKET": "Neutral Market → Watch/Research Only",
    }
    return labels.get(profile, "Market Reaction Evaluation")


def pitching_dominance_under_scores(info, opening_total, first_seen_total, live_total, scores, p, q, traffic):
    """
    V3.7.2 fifth profile: early true suppression.
    This is designed for games that open 7.5/8.0 because of quality starters, then
    stay dead through the first few innings. The goal is to catch UNDER before
    the market fully crushes the live total.
    """
    if not ENABLE_PITCHING_DOMINANCE_UNDER:
        return {}

    scores = scores or {}
    inning = safe_int((info or {}).get("inning"), 1)
    total_runs = safe_int((info or {}).get("total_runs"), safe_int((info or {}).get("away_runs"), 0) + safe_int((info or {}).get("home_runs"), 0))
    ref = safe_float(opening_total, None)
    if ref is None:
        ref = safe_float(first_seen_total, None)
    live = safe_float(live_total, None)
    if ref is None or live is None:
        return {"pitching_dominance_under_score": 0, "pitching_dominance_under_ok": False}

    move_down = ref - live
    dominance = safe_int(scores.get("dominance"), 0)
    run_prev = safe_int(scores.get("run_prevention"), 0)
    under_env = safe_int(scores.get("under_environment"), 0)
    k_env = safe_int(scores.get("strikeout_environment"), 0)
    bp_lock = safe_int(scores.get("bullpen_lockdown"), 0)
    stress = safe_int(scores.get("pitcher_stress"), 0)
    contact = safe_int(scores.get("contact_quality"), 50)
    p2r = safe_int(scores.get("pressure_to_runs"), 0)
    conv = safe_int(scores.get("run_conversion"), 0)
    traffic_conv = safe_int(scores.get("traffic_conversion"), 0)
    recent_baserunners = safe_int((traffic or {}).get("recent_baserunners"), 0)
    recent_hits = safe_int((traffic or {}).get("recent_hits"), 0)
    recent_walks = safe_int((traffic or {}).get("recent_walks"), 0)
    recent_ks = safe_int((traffic or {}).get("recent_strikeouts"), 0)
    hard_hit = safe_int((q or {}).get("hard_hit"), 0)
    barrels = safe_int((q or {}).get("barrels"), 0)
    bip = safe_int((q or {}).get("balls_in_play"), 0)
    whiff = safe_float((q or {}).get("whiff_pct"), 0)
    csw = safe_float((q or {}).get("csw_pct"), 0)
    strike_pct = safe_float((q or {}).get("strike_pct"), 0)
    pitch_count = safe_int((p or {}).get("pitch_count"), 0)
    outs_recorded = safe_int((p or {}).get("outs_recorded"), 0)
    walks = safe_int((p or {}).get("walks"), 0)
    hits = safe_int((p or {}).get("hits"), 0)

    score = 0
    if ref <= PITCHING_DOMINANCE_OPEN_MAX:
        score += 12
    if PITCHING_DOMINANCE_MIN_INNING <= inning <= PITCHING_DOMINANCE_MAX_INNING:
        score += 10
    if total_runs <= PITCHING_DOMINANCE_MAX_RUNS:
        score += 10
    if 0 <= move_down <= PITCHING_DOMINANCE_MAX_LIVE_DROP:
        score += 12
    elif move_down > PITCHING_DOMINANCE_MAX_LIVE_DROP:
        score -= 12  # market may already be too low
    elif move_down < 0:
        score -= 10  # total has not moved down; this is not the dominance-under setup yet

    score += dominance * 0.18
    score += run_prev * 0.16
    score += under_env * 0.14
    score += k_env * 0.12
    score += bp_lock * 0.08
    score += max(0, 55 - stress) * 0.16
    score += max(0, 50 - contact) * 0.16
    score += max(0, 70 - p2r) * 0.08
    score += max(0, 65 - conv) * 0.06
    score += max(0, 65 - traffic_conv) * 0.06
    score += max(0, PITCHING_DOMINANCE_MAX_RECENT_BASERUNNERS - recent_baserunners) * 4
    score += min(10, recent_ks * 3)

    if bip >= 4 and hard_hit <= 1:
        score += 8
    if barrels == 0 and bip >= 3:
        score += 6
    if whiff >= 25:
        score += 6
    if csw >= 28:
        score += 6
    if strike_pct >= 63:
        score += 5
    if outs_recorded >= 6 and pitch_count and pitch_count / max(1, outs_recorded) <= 5.0:
        score += 6
    if hits + walks <= 3 and outs_recorded >= 6:
        score += 6

    # Hard blockers / deductions for chaos hiding under a low score.
    if recent_baserunners > PITCHING_DOMINANCE_MAX_RECENT_BASERUNNERS:
        score -= 14
    if stress > PITCHING_DOMINANCE_MAX_STRESS:
        score -= 12
    if contact > PITCHING_DOMINANCE_MAX_CONTACT:
        score -= 10
    if p2r >= 85 or conv >= 85 or traffic_conv >= 75:
        score -= 12
    if walks >= 3:
        score -= 10

    score = round(clamp(score))
    ok = (
        ref <= PITCHING_DOMINANCE_OPEN_MAX
        and PITCHING_DOMINANCE_MIN_INNING <= inning <= PITCHING_DOMINANCE_MAX_INNING
        and total_runs <= PITCHING_DOMINANCE_MAX_RUNS
        and 0 <= move_down <= PITCHING_DOMINANCE_MAX_LIVE_DROP
        and score >= PITCHING_DOMINANCE_MIN_SCORE
        and stress <= PITCHING_DOMINANCE_MAX_STRESS
        and contact <= PITCHING_DOMINANCE_MAX_CONTACT
        and recent_baserunners <= PITCHING_DOMINANCE_MAX_RECENT_BASERUNNERS
        and (k_env >= PITCHING_DOMINANCE_MIN_K_ENV or dominance >= 65 or run_prev >= 75)
        and bp_lock >= PITCHING_DOMINANCE_MIN_BPLOCK
    )

    return {
        "pitching_dominance_under_score": score,
        "pitching_dominance_under_ok": ok,
        "pitching_dominance_live_drop": round(move_down, 1),
    }



# -----------------------------
# V3.4 lineup / bullpen / calibration helpers
# -----------------------------

_EV_CALIBRATION_CACHE = {"loaded_at": 0, "buckets": {}}



def market_reaction_side_gate(side, edge, scores, info):
    """
    V3.7.1 hard gate. The Market Reaction Engine should classify the market
    first, then the betting side must agree with that classification.

    This prevents the old pressure engine from still firing OVERs into inflated
    markets unless the game is a true continuation environment.
    """
    if not ENABLE_MARKET_REACTION_ENGINE:
        return True, "market reaction disabled"

    side = str(side or "").upper()
    scores = scores or {}
    profile = scores.get("market_reaction_profile", "NEUTRAL_MARKET")
    move = safe_float(scores.get("market_reaction_move"), 0)
    settle = safe_int(scores.get("settle_down_score"), 0)
    cont = safe_int(scores.get("continuation_score"), 0)
    p2r = safe_int(scores.get("pressure_to_runs"), 0)
    conv = safe_int(scores.get("run_conversion"), 0)
    traffic = safe_int(scores.get("traffic_conversion"), 0)
    contact = safe_int(scores.get("contact_quality"), 0)
    current_pressure = safe_int(scores.get("current_inning_pressure"), 0)
    inning = safe_int((info or {}).get("inning"), 1)

    if side == "OVER":
        if profile in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"]:
            return False, f"market reaction blocks OVER: {profile} settle={settle} cont={cont}"

        if INFLATED_OVER_REQUIRE_CONTINUATION and move >= INFLATED_TOTAL_MOVE_RUNS:
            continuation_ok = (
                profile == "CONTINUATION_OVER"
                and cont >= CONTINUATION_OVER_MIN_SCORE
                and abs(safe_float(edge, 0)) >= INFLATED_OVER_MIN_CONTINUATION_EDGE
                and p2r >= INFLATED_OVER_MIN_P2R
                and conv >= INFLATED_OVER_MIN_CONV
            )
            if not continuation_ok:
                return False, f"inflated OVER blocked: move={move:+.1f} profile={profile} cont={cont} p2r={p2r} conv={conv}"

        return True, "OVER agrees with market reaction"

    if side == "UNDER":
        if profile == "PITCHING_DOMINANCE_UNDER":
            return True, "pitching dominance UNDER approved"

        if profile == "CONTINUATION_OVER":
            return False, f"market reaction blocks UNDER: continuation={cont}"

        if profile in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"]:
            fade_ok = (
                move >= INFLATED_TOTAL_MOVE_RUNS
                and inning >= INFLATED_UNDER_MIN_INNING
                and safe_float(edge, 0) <= -INFLATED_UNDER_MIN_EDGE
                and settle >= SETTLE_DOWN_MIN_SCORE
                and (settle - cont) >= MARKET_REACTION_MIN_SCORE_GAP
                and cont <= CONTINUATION_MAX_FOR_FADE
                and current_pressure <= INFLATED_UNDER_ALLOW_CURRENT_PRESSURE
                and contact <= INFLATED_UNDER_ALLOW_CONTACT
                and traffic <= INFLATED_UNDER_MAX_TRAFFIC
                and (p2r <= INFLATED_UNDER_MAX_P2R_SOFT or settle >= 85)
            )
            if not fade_ok:
                return False, (
                    f"inflated UNDER not clean enough: move={move:+.1f} settle={settle} cont={cont} "
                    f"pressure={current_pressure} contact={contact} traffic={traffic} p2r={p2r}"
                )
            return True, "inflated UNDER agrees with market reaction"

        return True, "classic UNDER path"

    return True, "unknown side"


def annotate_market_reaction_block(scores, reason):
    scores = dict(scores or {})
    scores["market_reaction_block_reason"] = reason
    return scores


def batter_order_slot_score(slot):
    """Approximate hitter quality from batting-order slot when full hitter stats are unavailable."""
    slot = safe_int(slot, 0)
    if slot in [1, 2, 3, 4]:
        return 72
    if slot in [5, 6]:
        return 56
    if slot in [7, 8, 9]:
        return 38
    return 50


def lineup_pocket_score(info):
    """
    V3.4: score the next hitter pocket. If the MLB feed exposes batting-order info,
    use it. If not, degrade to a neutral/low-information estimate from current batter only.
    """
    if not ENABLE_V34_INTELLIGENCE:
        return 50
    pocket = info.get("next_batter_pocket") or []
    if not pocket:
        # Current batter only: better than nothing, but no strong conviction.
        slot = safe_int(info.get("current_batter_order_slot"), 0)
        return round(clamp(batter_order_slot_score(slot))) if slot else 50
    vals = []
    for b in pocket[:3]:
        vals.append(batter_order_slot_score(b.get("slot")))
    return round(clamp(sum(vals) / len(vals))) if vals else 50


def bullpen_context_score(info, scores):
    """
    V3.4 placeholder for bullpen quality/fatigue. Uses available live signals now;
    can be replaced later with true rest/usage data from a premium feed.
    Higher = more scoring risk / weaker run prevention.
    """
    if not ENABLE_V34_INTELLIGENCE:
        return 50
    inning = safe_int(info.get("inning"), 1)
    stress = safe_int((scores or {}).get("pitcher_stress"), 0)
    starter_exit = safe_int((scores or {}).get("starter_exit_probability"), 0)
    bp_lock = safe_int((scores or {}).get("bullpen_lockdown"), 50)
    kenv = safe_int((scores or {}).get("strikeout_environment"), 50)
    risk = 42
    if inning >= 5:
        risk += 8
    if inning >= 7:
        risk += 6
    risk += stress * 0.15
    risk += starter_exit * 0.12
    risk -= bp_lock * 0.18
    risk -= max(0, kenv - 50) * 0.10
    return round(clamp(risk))


def confidence_decay_score(state_game, info, opportunity):
    """
    V3.4: reduce trust when the current setup is likely stale. This matters for
    login/feed status and prevents borderline alerts after the situation changes.
    """
    if not ENABLE_V34_INTELLIGENCE or not opportunity:
        return 0
    decay = 0
    side = str(opportunity.get("side", "")).upper()
    outs = safe_int(info.get("outs"), 0)
    base_label = str((info.get("base_state") or {}).get("label", "")).lower()
    inning_state = str(info.get("inning_state") or "").lower()
    scores = opportunity.get("scores", {}) or {}

    if outs >= 3 or inning_state.startswith("middle") or inning_state.startswith("end"):
        decay += 28
    if side == "OVER" and ("bases empty" in base_label or base_label in ["", "empty"]):
        decay += 18
    if side == "OVER" and "runner on 1st" in base_label and outs >= 2:
        decay += 14
    if side == "UNDER" and safe_int(scores.get("pitcher_stress"), 0) >= 75:
        decay += 16
    if side == "UNDER" and safe_int(scores.get("pressure_to_runs"), 0) >= 55:
        decay += 14

    active = (state_game or {}).get("active_thesis") or {}
    if active:
        active_inning = safe_float(active.get("inning_float"), safe_float(active.get("inning"), 0))
        if inning_float(info) - active_inning >= 1.0:
            decay += 10
        if active.get("base_out") and base_label and base_label not in str(active.get("base_out", "")).lower():
            decay += 8
    return round(clamp(decay))


def edge_bucket(edge):
    e = abs(safe_float(edge, 0))
    if e < 1.0:
        return "0.0-1.0"
    if e < 1.5:
        return "1.0-1.5"
    if e < 2.0:
        return "1.5-2.0"
    if e < 3.0:
        return "2.0-3.0"
    if e < 4.0:
        return "3.0-4.0"
    return "4.0+"


def load_ev_calibration_buckets():
    """Use stored graded results to calibrate edge-to-win probability once sample is large enough."""
    now_ts = time.time()
    if now_ts - _EV_CALIBRATION_CACHE.get("loaded_at", 0) < EV_CALIBRATION_CACHE_SECONDS:
        return _EV_CALIBRATION_CACHE.get("buckets", {})
    buckets = {}
    if ENABLE_HISTORICAL_EV_CALIBRATION and os.path.exists(GRADED_RESULTS_FILE):
        for r in csv_read_rows(GRADED_RESULTS_FILE):
            if r.get("result") not in ["WIN", "LOSS"]:
                continue
            side = str(r.get("side", "")).upper()
            b = edge_bucket(r.get("edge"))
            key = (side, b)
            node = buckets.setdefault(key, {"wins": 0, "losses": 0})
            if r.get("result") == "WIN":
                node["wins"] += 1
            else:
                node["losses"] += 1
    _EV_CALIBRATION_CACHE["loaded_at"] = now_ts
    _EV_CALIBRATION_CACHE["buckets"] = buckets
    return buckets


def calibrated_model_probability(side, edge, fallback_prob):
    buckets = load_ev_calibration_buckets()
    key = (str(side or "").upper(), edge_bucket(edge))
    node = buckets.get(key)
    if not node:
        return fallback_prob, "formula"
    sample = safe_int(node.get("wins"), 0) + safe_int(node.get("losses"), 0)
    if sample < MIN_EV_CALIBRATION_SAMPLE:
        return fallback_prob, f"formula/sample {sample}"
    raw = safe_int(node.get("wins"), 0) / max(1, sample)
    # Blend to avoid overreacting to one bucket.
    blended = (raw * 0.70) + (safe_float(fallback_prob, 0.5) * 0.30)
    return round(max(0.38, min(0.72, blended)), 4), f"historical {node.get('wins')}-{node.get('losses')}"


def leading_book_score(side, velocity_info):
    if not ENABLE_V34_INTELLIGENCE:
        return 50
    side = str(side or "").upper()
    leading = str((velocity_info or {}).get("leading_book") or "").lower()
    move = 0
    for v in ((velocity_info or {}).get("book_velocities") or {}).values():
        if str(v.get("book") or "").lower() == leading:
            move = safe_float(v.get("move"), 0)
            break
    score = 50
    if leading:
        if any(pb in leading for pb in PREFERRED_BOOKS):
            score += PREFERRED_LEADING_BOOK_BONUS
        if side == "OVER" and move > 0:
            score += min(18, move * 12)
        if side == "UNDER" and move < 0:
            score += min(18, abs(move) * 12)
        if side == "OVER" and move < 0:
            score -= min(18, abs(move) * 12)
        if side == "UNDER" and move > 0:
            score -= min(18, move * 12)
    return round(clamp(score))

# -----------------------------
# V3.2 practical betting math
# -----------------------------

def american_to_implied_probability(price):
    """Return break-even probability for American odds."""
    p = safe_int(price, 0)
    if p == 0:
        return None
    if p > 0:
        return round(100.0 / (p + 100.0), 4)
    return round(abs(p) / (abs(p) + 100.0), 4)


def run_edge_to_model_probability(side, edge):
    """
    Convert run edge into a conservative rough probability estimate.
    This is not a true distribution model; it is an EV sanity check so the bot
    does not blindly accept bad prices. One run of edge is treated as about
    RUN_EDGE_TO_PROB_PER_RUN above 50%, capped to avoid false precision.
    """
    side = str(side or "").upper()
    e = safe_float(edge, 0)
    directional_edge = e if side == "OVER" else -e
    prob = 0.50 + directional_edge * RUN_EDGE_TO_PROB_PER_RUN
    return round(max(0.38, min(0.72, prob)), 4)


def expected_value_per_unit(side, edge, price):
    """Expected profit per 1 unit risked, with V3.4 historical calibration when enough samples exist."""
    formula_prob = run_edge_to_model_probability(side, edge)
    model_prob, prob_source = calibrated_model_probability(side, edge, formula_prob)
    breakeven = american_to_implied_probability(price)
    if breakeven is None:
        return {"model_probability": model_prob, "break_even_probability": None, "expected_value": None, "probability_source": prob_source}
    p = safe_int(price, 0)
    win_profit = (p / 100.0) if p > 0 else (100.0 / abs(p))
    ev = model_prob * win_profit - (1 - model_prob)
    return {
        "model_probability": round(model_prob, 4),
        "break_even_probability": round(breakeven, 4),
        "expected_value": round(ev, 4),
        "probability_source": prob_source,
    }


def parse_iso_timestamp(ts):
    if not ts:
        return None
    try:
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).timestamp()
    except Exception:
        return None


def book_line_age_seconds(book_line):
    """Age of the book's last update if the provider supplies a timestamp."""
    if not book_line:
        return None
    ts = parse_iso_timestamp(book_line.get("last_update"))
    if ts is None:
        return None
    return max(0, round(time.time() - ts))



def max_entry_line_for(opportunity):
    """
    Hard no-chase line for the user. This turns BET NOW into a practical app instruction.
    OVER: do not enter if the total rises above line + buffer.
    UNDER: do not enter if the total falls below line - buffer.
    """
    if not opportunity:
        return None
    side = str(opportunity.get("side", "")).upper()
    line = safe_float(opportunity.get("line"), None)
    if line is None:
        return None
    if side == "OVER":
        return round(line + MAX_ENTRY_HALF_RUN_BUFFER, 1)
    if side == "UNDER":
        return round(line - MAX_ENTRY_HALF_RUN_BUFFER, 1)
    return line


def max_entry_price_for(side):
    side = str(side or "").upper()
    return MAX_ENTRY_PRICE_OVER if side == "OVER" else MAX_ENTRY_PRICE_UNDER


def v33_market_first_block_reason(info, market_context, opportunity):
    """
    Market-first gate. We only let baseball logic speak after the market/app line is playable.
    This is designed to reduce bad bets and chasing stale numbers.
    """
    if not ENABLE_V33_ROI_DISCIPLINE or not opportunity:
        return None
    side = str(opportunity.get("side", "")).upper()
    price = safe_int(opportunity.get("price"), 0)
    edge = abs(safe_float(opportunity.get("edge"), 0))
    book_count = safe_int((market_context or {}).get("book_count"), 0)
    mconf = safe_int((market_context or {}).get("market_confirmation_score"), 50)
    age = safe_float((market_context or {}).get("best_line_age_seconds"), None)
    ev = expected_value_per_unit(side, opportunity.get("edge"), opportunity.get("price")).get("expected_value")

    if book_count >= MARKET_FIRST_MIN_BOOKS and mconf < MARKET_FIRST_MIN_CONFIRMATION:
        return f"market-first block: confirmation {mconf} below {MARKET_FIRST_MIN_CONFIRMATION}"
    if age is not None and age > MAX_BEST_LINE_AGE_SECONDS:
        return f"market-first block: best line stale ({int(age)}s)"
    if ev is not None and ev < MARKET_FIRST_MIN_EV:
        return f"market-first block: EV {ev:+.3f} below ROI minimum {MARKET_FIRST_MIN_EV:+.3f}"
    max_price = max_entry_price_for(side)
    if price and price < max_price and edge < 3.0:
        return f"market-first block: price {price} too expensive without elite edge"
    return None


def v33_baseball_quality_block_reason(info, opportunity):
    """Extra baseball discipline to avoid fragile live-total bets."""
    if not ENABLE_V33_ROI_DISCIPLINE or not opportunity:
        return None
    side = str(opportunity.get("side", "")).upper()
    scores = opportunity.get("scores", {}) or {}
    inning = safe_int(info.get("inning"), 0)
    outs = safe_int(info.get("outs"), 0)
    edge_abs = abs(safe_float(opportunity.get("edge"), 0))
    conf = safe_int(opportunity.get("confidence"), safe_int(scores.get("confirmation_score"), 0))
    p2r = safe_int(scores.get("pressure_to_runs"), 0)
    tconv = safe_int(scores.get("traffic_conversion"), 0)
    stress = safe_int(scores.get("pitcher_stress"), 0)
    run_prev = safe_int(scores.get("run_prevention"), 0)
    kenv = safe_int(scores.get("strikeout_environment"), 0)
    bp = safe_int(scores.get("bullpen_lockdown"), 0)
    lineup_score = lineup_pocket_score(info)
    bullpen_risk = bullpen_context_score(info, scores)

    base_label = str((info.get("base_state") or {}).get("label", "")).lower()
    weak_traffic = ("bases empty" in base_label) or ("runner on 1st" in base_label and outs >= 2)

    # V3.7.1: market-overreaction UNDER is a different animal than classic
    # run-prevention UNDER. If the market-reaction gate approves it, do not
    # force the old K/bullpen/run-prevention thresholds to also be perfect.
    if side == "UNDER" and scores.get("market_reaction_profile") in ["INFLATED_UNDER", "FALSE_INFLATION_FADE", "PITCHING_DOMINANCE_UNDER"]:
        ok, reason = market_reaction_side_gate("UNDER", opportunity.get("edge"), scores, info)
        if ok:
            return None
        return reason

    if side == "OVER":
        if inning < EARLY_OVER_MIN_INNING and not (p2r >= EARLY_OVER_MIN_P2R and tconv >= EARLY_OVER_MIN_TRAFFIC and stress >= EARLY_OVER_MIN_STRESS):
            return "baseball block: early OVER lacks elite pressure/traffic/stress"
        if lineup_score < WEAK_LINEUP_SCORE_BLOCK and not (p2r >= WEAK_LINEUP_MIN_P2R and tconv >= WEAK_LINEUP_MIN_CONV):
            return f"baseball block: weak lineup pocket ({lineup_score}) requires elite P2R/Conv"
        if lineup_score < LINEUP_POCKET_MIN_OVER and p2r < 90:
            return f"baseball block: OVER lacks lineup-pocket support ({lineup_score})"
        if bullpen_risk < BULLPEN_CONTEXT_MIN_OVER and inning >= 5 and p2r < 85:
            return f"baseball block: OVER lacks bullpen/fatigue support ({bullpen_risk})"
        if weak_traffic and p2r < 85:
            return "baseball block: OVER profile too fragile with weak traffic"
        if inning >= LATE_OVER_MAX_INNING and not (edge_abs >= LATE_OVER_MIN_EDGE and conf >= LATE_OVER_MIN_CONFIRMATION):
            return "baseball block: late OVER needs elite edge and confirmation"

    if side == "UNDER":
        if inning < UNDER_DEFAULT_MIN_INNING_V33 and not (run_prev >= 85 and kenv >= 75 and bp >= 65):
            return "baseball block: early UNDER needs elite run prevention/K/bullpen profile"
        if run_prev < UNDER_MIN_RUN_PREVENTION_V33 and (kenv < UNDER_MIN_KENV_V33 or bp < UNDER_MIN_BPLOCK_V33):
            return "baseball block: UNDER lacks run-prevention support"
        if lineup_score > LINEUP_POCKET_MAX_UNDER_RISK and run_prev < 88:
            return f"baseball block: UNDER facing dangerous lineup pocket ({lineup_score})"
        if (100 - bullpen_risk) < BULLPEN_CONTEXT_MIN_UNDER and inning >= 6:
            return f"baseball block: UNDER lacks bullpen lockdown context ({100 - bullpen_risk})"
    return None


def classify_bet_quality(row):
    """
    Separates bet quality from game result. A loss can be a good bet; a win can be a bad bet.
    """
    result = str(row.get("result", "")).upper()
    ev = safe_float(row.get("expected_value"), 0)
    mconf = safe_int(row.get("market_confirmation_score"), 0)
    risk = safe_int(row.get("risk_filter_score"), 0)
    clv = safe_float(row.get("clv", 0), 0)
    positive_market = mconf >= MARKET_FIRST_MIN_CONFIRMATION
    positive_ev = ev >= MARKET_FIRST_MIN_EV
    low_risk = risk <= V26_MAX_RISK_SCORE
    beat_clv = clv >= GOOD_BET_CLV_THRESHOLD

    if positive_ev and positive_market and low_risk:
        label = "GOOD_BET"
    elif beat_clv and positive_market:
        label = "GOOD_BET_CLV"
    elif result == "WIN":
        label = "GOOD_RESULT_QUESTIONABLE_BET"
    else:
        label = "BAD_BET_REVIEW"
    reason = f"EV {ev:+.3f} | MktConf {mconf} | Risk {risk} | CLV {clv:+.1f}"
    return label, reason

def recommended_app_status(opportunity, market_context):
    """Short practical status for real live betting apps."""
    if not opportunity:
        return "NO BET — no qualifying play"
    profile = market_reaction_profile_from_scores((opportunity or {}).get("scores", {}), (opportunity or {}).get("scenario"))
    if ENABLE_NEUTRAL_MARKET_WATCH_ONLY and profile == "NEUTRAL_MARKET":
        return "WATCH ONLY — NEUTRAL_MARKET research bucket, no SMS bet"
    price = opportunity.get("price")
    side = str(opportunity.get("side", "")).upper()
    line = opportunity.get("line")
    book = opportunity.get("recommended_book") or market_context.get("recommended_book") or market_context.get("price_adjusted_best_book") or market_context.get("best_book")
    age = safe_int(market_context.get("best_line_age_seconds"), 0)
    if age and age > APP_STATUS_STALE_SECONDS:
        return "NO BET — best line may be stale"
    ev = expected_value_per_unit(side, opportunity.get("edge"), price).get("expected_value")
    if ev is not None and ev < MIN_EXPECTED_VALUE:
        return "NO BET — price/EV not playable"
    if book:
        return f"BET NOW — only if {book} still shows {side} {line} or better"
    return f"BET NOW — only if your app still shows {side} {line} or better"


def apply_price_adjusted_best_line(info, market_context, opportunity):
    """
    V3.2 practical rewrite: the recommended play should be the best app line,
    not the primary-book line that triggered the model.
    """
    if not ENABLE_V32_BEST_LINE_REWRITE or not opportunity:
        return opportunity
    side = str(opportunity.get("side", "")).upper()
    best_line = market_context.get("price_adjusted_best_total") or market_context.get("best_available_total")
    best_price = market_context.get("price_adjusted_best_price") or market_context.get("best_available_price")
    best_book = market_context.get("price_adjusted_best_book") or market_context.get("best_book")
    if best_line is None:
        if REQUIRE_PLAYABLE_BOOK_FOR_STRIKE and USER_PLAYABLE_BOOKS:
            opportunity["action"] = "NO_PLAY"
            opportunity["app_status"] = "NO BET — no playable BetMGM/user-book line available"
        return opportunity
    best_line = safe_float(best_line, None)
    if best_line is None:
        if REQUIRE_PLAYABLE_BOOK_FOR_STRIKE and USER_PLAYABLE_BOOKS:
            opportunity["action"] = "NO_PLAY"
            opportunity["app_status"] = "NO BET — no playable BetMGM/user-book line available"
        return opportunity

    # Use the same projected total but recompute the actionable edge to the line a user can bet.
    projected = safe_float(opportunity.get("projected_total", opportunity.get("projection")), None)
    if projected is None:
        return opportunity
    new_edge = round(projected - best_line, 1)
    # If the best-app line flips the edge below the basic watch threshold, it is no longer practical.
    if side == "OVER" and new_edge < MIN_WATCH_EDGE_RUNS:
        opportunity["action"] = "NO_PLAY"
        opportunity["app_status"] = "NO BET — best available app line killed the edge"
        return opportunity
    if side == "UNDER" and new_edge > -MIN_WATCH_EDGE_RUNS:
        opportunity["action"] = "NO_PLAY"
        opportunity["app_status"] = "NO BET — best available app line killed the edge"
        return opportunity
    if not price_ok(best_price, abs(new_edge)):
        opportunity["action"] = "NO_PLAY"
        opportunity["app_status"] = "NO BET — best available price outside playable range"
        return opportunity

    if is_ignored_recommendation_book(best_book):
        opportunity["action"] = "NO_PLAY"
        opportunity["app_status"] = f"NO BET — ignored book removed from recommendation ({best_book})"
        return opportunity
    if REQUIRE_PLAYABLE_BOOK_FOR_STRIKE and USER_PLAYABLE_BOOKS and not is_user_playable_book(best_book):
        opportunity["action"] = "NO_PLAY"
        opportunity["app_status"] = "NO BET — no playable BetMGM/user-book line available"
        return opportunity

    ev = expected_value_per_unit(side, new_edge, best_price)
    opportunity["line"] = best_line
    opportunity["price"] = best_price
    opportunity["edge"] = new_edge
    opportunity["edge_grade"] = edge_grade(new_edge)
    opportunity["recommended_book"] = best_book
    opportunity["recommended_total"] = best_line
    opportunity["recommended_price"] = best_price
    opportunity.update(ev)
    opportunity["max_entry_line"] = max_entry_line_for(opportunity)
    opportunity["max_entry_price"] = max_entry_price_for(side)
    opportunity["app_status"] = recommended_app_status(opportunity, market_context)
    return opportunity


def market_quality_tags(row):
    """Bucket results by market-intelligence quality for the daily report."""
    tags = []
    if safe_int(row.get("market_confirmation_score"), 0) >= MIN_MARKET_CONFIRMATION_SCORE:
        tags.append("MARKET_CONFIRMED")
    else:
        tags.append("MARKET_NOT_CONFIRMED")
    if row.get("price_adjusted_best_total") and row.get("recommended_total"):
        tags.append("BEST_LINE_USED")
    if abs(safe_float(row.get("line_velocity"), 0)) >= LINE_VELOCITY_STRONG_MOVE:
        tags.append("HIGH_VELOCITY")
    if safe_float(row.get("market_disagreement"), 0) >= MARKET_DISAGREEMENT_STRONG:
        tags.append("BOOK_DISAGREEMENT")
    if safe_float(row.get("expected_value"), -1) >= MIN_EXPECTED_VALUE:
        tags.append("POSITIVE_EV")
    return tags


def summarize_bucket(label, rows):
    w, l, p, pct, units = summarize_record(rows)
    return f"{label}: {w}-{l}-{p} | {pct}% | {units:+.2f}u"

def tracking_webhook_enabled():
    if not ENABLE_TRACKING_WEBHOOK or not TRACKING_WEBHOOK_URL:
        return False
    try:
        parsed = urlparse(TRACKING_WEBHOOK_URL)
        return parsed.scheme in ["http", "https"] and bool(parsed.netloc)
    except Exception:
        return False


def post_tracking_event(event_type, payload):
    """
    Optional durable storage mirror.
    Use TRACKING_WEBHOOK_URL for a Google Sheets Apps Script, Zapier/Make webhook,
    Supabase Edge Function, or any endpoint that accepts JSON.
    This does not replace local CSV; it mirrors important rows off Railway storage.
    """
    if not tracking_webhook_enabled():
        return False

    body = {
        "event_type": event_type,
        "sent_at": now_local().isoformat(),
        "source": "SHIFT_MLB_V3_7_1_MARKET_REACTION",
        "payload": payload,
    }
    headers = {"Content-Type": "application/json"}
    if TRACKING_WEBHOOK_SECRET:
        headers["X-SHIFT-SECRET"] = TRACKING_WEBHOOK_SECRET
    try:
        r = requests.post(TRACKING_WEBHOOK_URL, json=body, headers=headers, timeout=10)
        if 200 <= r.status_code < 300:
            print(f"TRACKING WEBHOOK SENT | {event_type}")
            return True
        print(f"TRACKING WEBHOOK ERROR | {event_type} | {r.status_code} | {r.text[:180]}")
    except Exception as e:
        print(f"TRACKING WEBHOOK EXCEPTION | {event_type}:", repr(e))
    return False


def master_score_from_scores(side, scores):
    """
    Reduces duplicate score noise into one master thesis score.
    It does not replace detailed metrics; it gives the report a cleaner read.
    """
    side = str(side or "").upper()
    if side == "OVER":
        vals = [
            safe_int(scores.get("pressure_to_runs"), 0),
            safe_int(scores.get("run_conversion"), 0),
            safe_int(scores.get("traffic_conversion"), 0),
            safe_int(scores.get("hard_hit_efficiency"), 0),
            safe_int(scores.get("pitcher_stress"), 0),
        ]
        weights = [0.24, 0.24, 0.20, 0.18, 0.14]
    else:
        vals = [
            safe_int(scores.get("run_prevention"), 0),
            safe_int(scores.get("strikeout_environment"), 0),
            safe_int(scores.get("bullpen_lockdown"), 0),
            safe_int(scores.get("hard_hit_under_support"), 0),
            safe_int(scores.get("under_environment"), 0),
        ]
        weights = [0.24, 0.22, 0.20, 0.18, 0.16]
    return round(clamp(sum(v * w for v, w in zip(vals, weights))))


def master_market_value_score(opportunity, scores):
    edge = abs(safe_float((opportunity or {}).get("edge"), 0))
    proj = safe_int(scores.get("projection_score"), 0)
    pred = safe_int(scores.get("predictive_market_move"), 0)
    lag = safe_int(scores.get("market_lag"), 0)
    raw = (min(edge, 3.0) / 3.0) * 40 + proj * 0.30 + pred * 0.18 + lag * 0.12
    return round(clamp(raw))


def master_risk_filter_score(opportunity, info, scores):
    """
    Higher score means higher risk / more caution.
    Used for reports and result texts, not as a hard stop by itself.
    """
    side = str((opportunity or {}).get("side", "")).upper()
    line = safe_float((opportunity or {}).get("line"), 0)
    inning = safe_int((info or {}).get("inning"), 0)
    fake = safe_int(scores.get("fake_pressure"), 0)
    blowout = safe_int(scores.get("blowout_kill"), 0)
    k_env = safe_int(scores.get("strikeout_environment"), 0)
    bp = safe_int(scores.get("bullpen_lockdown"), 0)
    tconv = safe_int(scores.get("traffic_conversion"), 0)
    hheff = safe_int(scores.get("hard_hit_efficiency"), 0)

    risk = 0
    if line >= MAX_EXTREME_TOTAL_STRIKE_LINE:
        risk += 28
    if side == "OVER" and inning >= 7:
        risk += 22
    if side == "OVER" and k_env >= 65 and bp >= 55:
        risk += 22
    if side == "OVER" and tconv < 45 and hheff < 45:
        risk += 18
    if side == "UNDER" and k_env < 50 and bp < 45:
        risk += 18
    if side == "UNDER" and scores.get("market_reaction_profile") == "PITCHING_DOMINANCE_UNDER":
        risk -= 18
    risk += fake * 0.12 + blowout * 0.10
    return round(clamp(risk))

def csv_append_once(path, fieldnames, row):
    """
    Lightweight local CSV logging. No extra API calls / no extra credits.
    """
    exists = os.path.exists(path)
    try:
        with open(path, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            if not exists:
                writer.writeheader()
            writer.writerow(row)
    except Exception as e:
        print(f"CSV LOG ERROR {path}:", repr(e))



def csv_read_rows(path):
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", newline="") as f:
            return list(csv.DictReader(f))
    except Exception as e:
        print(f"CSV READ ERROR {path}:", repr(e))
        return []


def csv_write_rows(path, fieldnames, rows):
    try:
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({k: row.get(k, "") for k in fieldnames})
    except Exception as e:
        print(f"CSV WRITE ERROR {path}:", repr(e))



# ---------------------------------------------------------------------------
# V3.9.0 Decision Database + Adaptive Learning
# ---------------------------------------------------------------------------

def decision_log_fieldnames():
    return [
        "decision_id", "timestamp", "date", "game_key", "game_pk", "game",
        "action", "decision_type", "reject_reason",
        "profile", "scenario", "side", "line", "price", "book",
        "opening_total", "first_seen_total", "true_opening_total", "live_total",
        "projected_total", "edge", "confidence", "expected_value",
        "inning", "inning_state", "outs", "score", "base_out",
        "projection_score", "confirmation_score", "market_confirmation_score",
        "market_value_score", "risk_filter_score", "calculated_risk_tier", "suggested_unit",
        "pressure_to_runs", "run_conversion", "traffic_conversion", "pitcher_stress",
        "contact_quality", "bullpen_risk", "run_prevention", "strikeout_environment",
        "bullpen_lockdown", "settle_down_score", "continuation_score",
        "discounted_over_score", "false_inflation_score", "continuation_exhaustion_score",
        "pitching_dominance_under_score", "market_reaction_move", "market_discount",
        "consensus_total", "market_min_total", "market_max_total", "market_disagreement",
        "line_velocity", "line_direction", "book_count", "recommended_book",
        "recommended_total", "recommended_price", "best_line_age_seconds",
        "adaptive_status", "adaptive_confidence_adjustment", "adaptive_sample",
        "adaptive_roi", "adaptive_avg_clv",
        "pattern_tags", "bet_quality", "quality_reason",
        "final_score", "final_total", "result", "units", "clv", "graded_at",
    ]


def decision_game_label(info):
    return f"{info.get('away', '')} at {info.get('home', '')}".strip(" at")


def decision_action_from_opportunity(opportunity, approved=False, reason=""):
    if not opportunity:
        return "NO_OPPORTUNITY"
    if approved:
        tier = str(opportunity.get("calculated_risk_tier") or "").upper()
        unit = str(opportunity.get("suggested_unit") or "").upper()
        if tier == "C" or "TEST" in unit:
            return "TEST_UNIT"
        return "BET_NOW"
    action = str(opportunity.get("action") or "").upper()
    reason_u = str(reason or "").upper()
    if action == "STRIKE":
        return "NO_BET"
    if "WATCH" in action or "RESEARCH" in action or "WATCH" in reason_u or "RESEARCH" in reason_u:
        return "RESEARCH_ONLY"
    return "NO_BET"


def decision_log_id(info, opportunity, action, reason=""):
    """
    Stable enough to prevent duplicate poll spam, but specific enough to capture
    materially different decisions as game state changes.
    """
    info = info or {}
    opportunity = opportunity or {}
    scores = opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    side = str(opportunity.get("side") or "NONE").upper()
    line = str(opportunity.get("line") or "")
    inning = safe_int(info.get("inning"), 0)
    outs = safe_int(info.get("outs"), 0)
    reason_key = str(reason or "").lower()[:60]
    return "|".join([
        today(), str(info.get("game_pk") or decision_game_label(info)), action,
        profile, side, line, str(inning), str(outs), reason_key,
    ])


def should_log_decision(state_game, decision_id, action):
    if not decision_id:
        return False
    state_game = state_game if isinstance(state_game, dict) else {}
    cache = state_game.setdefault("decision_log_cache", {})
    last = safe_float(cache.get(decision_id), 0)
    now_ts = time.time()
    cooldown = DECISION_LOG_ACCEPT_COOLDOWN_SECONDS if action in ["BET_NOW", "TEST_UNIT"] else DECISION_LOG_REJECT_COOLDOWN_SECONDS
    if last and (now_ts - last) < cooldown:
        return False
    cache[decision_id] = now_ts
    return True


def decision_row_from_opportunity(info, market_context, opportunity, action, decision_type="", reject_reason=""):
    info = info or {}
    market_context = market_context or {}
    opportunity = opportunity or {}
    scores = opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    tier = opportunity.get("calculated_risk_tier") or calculated_risk_tier(opportunity, market_context) if opportunity else ""
    row = {
        "decision_id": decision_log_id(info, opportunity, action, reject_reason),
        "timestamp": now_local().isoformat(),
        "date": today(),
        "game_key": game_key_from_info(info) if info else "",
        "game_pk": info.get("game_pk", ""),
        "game": decision_game_label(info),
        "action": action,
        "decision_type": decision_type,
        "reject_reason": reject_reason,
        "profile": profile,
        "scenario": opportunity.get("scenario", ""),
        "side": opportunity.get("side", ""),
        "line": opportunity.get("line", opportunity.get("recommended_total", "")),
        "price": opportunity.get("price", opportunity.get("recommended_price", "")),
        "book": opportunity.get("recommended_book") or market_context.get("recommended_book") or market_context.get("primary_book") or "",
        "opening_total": market_context.get("opening_total"),
        "first_seen_total": market_context.get("first_seen_total"),
        "true_opening_total": market_context.get("true_opening_total"),
        "live_total": market_context.get("live_total", opportunity.get("line", "")),
        "projected_total": opportunity.get("projection", opportunity.get("projected_total", "")),
        "edge": opportunity.get("edge", ""),
        "confidence": opportunity.get("confidence", ""),
        "expected_value": opportunity.get("expected_value", ""),
        "inning": info.get("inning", ""),
        "inning_state": info.get("inning_state", ""),
        "outs": info.get("outs", ""),
        "score": score_text(info) if info else "",
        "base_out": f"{(info.get('base_state') or {}).get('label', '')}, {info.get('outs', '')} out(s)",
        "projection_score": scores.get("projection_score"),
        "confirmation_score": scores.get("confirmation_score"),
        "market_confirmation_score": market_context.get("market_confirmation_score", opportunity.get("market_confirmation_score")),
        "market_value_score": master_market_value_score(opportunity, scores) if opportunity else "",
        "risk_filter_score": master_risk_filter_score(opportunity, info, scores) if opportunity else "",
        "calculated_risk_tier": tier,
        "suggested_unit": tier_unit_guidance(tier) if tier else "",
        "pressure_to_runs": scores.get("pressure_to_runs"),
        "run_conversion": scores.get("run_conversion"),
        "traffic_conversion": scores.get("traffic_conversion"),
        "pitcher_stress": scores.get("pitcher_stress"),
        "contact_quality": scores.get("contact_quality"),
        "bullpen_risk": scores.get("bullpen_risk"),
        "run_prevention": scores.get("run_prevention"),
        "strikeout_environment": scores.get("strikeout_environment"),
        "bullpen_lockdown": scores.get("bullpen_lockdown"),
        "settle_down_score": scores.get("settle_down_score"),
        "continuation_score": scores.get("continuation_score"),
        "discounted_over_score": scores.get("discounted_over_score"),
        "false_inflation_score": scores.get("false_inflation_score"),
        "continuation_exhaustion_score": scores.get("continuation_exhaustion_score"),
        "pitching_dominance_under_score": scores.get("pitching_dominance_under_score"),
        "market_reaction_move": scores.get("market_reaction_move"),
        "market_discount": market_context.get("market_discount"),
        "consensus_total": market_context.get("consensus_total"),
        "market_min_total": market_context.get("market_min_total"),
        "market_max_total": market_context.get("market_max_total"),
        "market_disagreement": market_context.get("market_disagreement"),
        "line_velocity": market_context.get("line_velocity"),
        "line_direction": market_context.get("line_direction"),
        "book_count": market_context.get("book_count"),
        "recommended_book": opportunity.get("recommended_book") or market_context.get("recommended_book"),
        "recommended_total": opportunity.get("recommended_total") or opportunity.get("line"),
        "recommended_price": opportunity.get("recommended_price") or opportunity.get("price"),
        "best_line_age_seconds": market_context.get("best_line_age_seconds"),
        "adaptive_status": opportunity.get("adaptive_status"),
        "adaptive_confidence_adjustment": opportunity.get("adaptive_confidence_adjustment"),
        "adaptive_sample": opportunity.get("adaptive_sample"),
        "adaptive_roi": opportunity.get("adaptive_roi"),
        "adaptive_avg_clv": opportunity.get("adaptive_avg_clv"),
        "bet_quality": opportunity.get("bet_quality"),
        "quality_reason": opportunity.get("quality_reason"),
        "final_score": "",
        "final_total": "",
        "result": "PENDING",
        "units": "",
        "clv": "",
        "graded_at": "",
    }
    row["pattern_tags"] = "|".join(pattern_tags_from_row({
        **row,
        "p2r": row.get("pressure_to_runs"),
        "conv": row.get("run_conversion"),
        "stress": row.get("pitcher_stress"),
        "contact": row.get("contact_quality"),
        "pred_move": scores.get("predictive_market_move"),
        "threat_index": scores.get("threat_index"),
    }))
    if not row.get("bet_quality"):
        bq, qr = classify_bet_quality(row)
        row["bet_quality"] = bq
        row["quality_reason"] = qr
    return row


def log_shift_decision(state_game, info, market_context, opportunity, action=None, decision_type="", reject_reason=""):
    """
    Master decision logger: BET_NOW, TEST_UNIT, NO_BET, RESEARCH_ONLY.
    This is the data backbone for daily collection, long-term evaluation,
    adaptive confidence, and self-optimization.
    """
    if not ENABLE_DECISION_LOG or not opportunity:
        return
    action = action or decision_action_from_opportunity(opportunity, approved=False, reason=reject_reason)
    if action == "NO_BET" and not ENABLE_DECISION_LOG_NO_BETS:
        return
    if action == "RESEARCH_ONLY" and not ENABLE_DECISION_LOG_RESEARCH:
        return
    row = decision_row_from_opportunity(info, market_context, opportunity, action, decision_type, reject_reason)
    if not should_log_decision(state_game if isinstance(state_game, dict) else {}, row.get("decision_id"), action):
        return
    csv_append_once(DECISION_LOG_FILE, decision_log_fieldnames(), row)
    post_tracking_event("shift_decision", row)
    try:
        post_existing_tab_decision_exports(row)
    except Exception as e:
        print("IMPORTANT TAB DECISION EXPORT ERROR:", repr(e))
    print(f"DECISION LOG | {action} | {row.get('game')} | {row.get('side')} {row.get('line')} | {row.get('profile')} | {reject_reason or decision_type}")


def load_adaptive_config():
    if not ENABLE_ADAPTIVE_CONFIG:
        return {}
    if not os.path.exists(ADAPTIVE_CONFIG_FILE):
        return {}
    try:
        with open(ADAPTIVE_CONFIG_FILE, "r") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print("ADAPTIVE CONFIG LOAD ERROR:", repr(e))
        return {}


def save_adaptive_config(config):
    if not ENABLE_ADAPTIVE_CONFIG:
        return False
    try:
        with open(ADAPTIVE_CONFIG_FILE, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print("ADAPTIVE CONFIG SAVE ERROR:", repr(e))
        return False


def adaptive_rows_for_learning():
    rows = csv_read_rows(DECISION_LOG_FILE)
    return [
        r for r in rows
        if r.get("result") in ["WIN", "LOSS", "PUSH"]
        and r.get("action") in ["BET_NOW", "TEST_UNIT"]
    ]


def build_adaptive_config_from_results():
    rows = adaptive_rows_for_learning()
    profiles = {}
    for r in rows:
        profile = r.get("profile") or r.get("market_reaction_profile") or "UNCLASSIFIED"
        profiles.setdefault(profile, []).append(r)

    config = {}
    for profile, bucket in sorted(profiles.items()):
        w, l, p, pct, units = summarize_record(bucket)
        graded_count = max(1, w + l)
        roi = round(units / graded_count, 4)
        clvs = [safe_float(x.get("clv"), 0) for x in bucket if str(x.get("clv", "")).strip() not in ["", "None"]]
        avg_clv = round(avg(clvs), 2) if clvs else 0.0
        sample = w + l + p

        if sample < MIN_ADAPTIVE_SAMPLE:
            status = "OPEN_TEST"
            conf_adj = 0
            tier_bias = "none"
        elif roi >= ADAPTIVE_STRONG_ROI and avg_clv >= ADAPTIVE_STRONG_CLV:
            status = "PROVEN"
            conf_adj = ADAPTIVE_PROVEN_CONF_BONUS
            tier_bias = "upgrade"
        elif roi <= (ADAPTIVE_WEAK_ROI * 2) or avg_clv <= (ADAPTIVE_WEAK_CLV * 2):
            status = "FAILING"
            conf_adj = -ADAPTIVE_FAILING_CONF_PENALTY
            tier_bias = "downgrade_hard"
        elif roi <= ADAPTIVE_WEAK_ROI or avg_clv <= ADAPTIVE_WEAK_CLV:
            status = "TIGHTEN"
            conf_adj = -ADAPTIVE_TIGHTEN_CONF_PENALTY
            tier_bias = "downgrade"
        else:
            status = "HOLD"
            conf_adj = 0
            tier_bias = "none"

        config[profile] = {
            "sample": sample, "wins": w, "losses": l, "pushes": p,
            "win_pct": pct, "units": units, "roi": roi, "avg_clv": avg_clv,
            "status": status, "confidence_adjustment": conf_adj,
            "tier_bias": tier_bias, "updated_at": now_local().isoformat(),
        }
    save_adaptive_config(config)
    return config


def apply_adaptive_adjustment(opportunity):
    """
    Controlled self-optimization: the model can only make small confidence
    adjustments after profile samples prove themselves in the decision database.
    """
    if not ENABLE_ADAPTIVE_CONFIDENCE or not opportunity:
        return opportunity
    scores = opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    config = load_adaptive_config()
    profile_cfg = config.get(profile) or {}
    if not profile_cfg:
        return opportunity

    adj = safe_int(profile_cfg.get("confidence_adjustment"), 0)
    opportunity = dict(opportunity)
    opportunity["confidence"] = round(clamp(safe_int(opportunity.get("confidence"), 0) + adj))
    opportunity["adaptive_status"] = profile_cfg.get("status", "")
    opportunity["adaptive_confidence_adjustment"] = adj
    opportunity["adaptive_sample"] = profile_cfg.get("sample", 0)
    opportunity["adaptive_roi"] = profile_cfg.get("roi", "")
    opportunity["adaptive_avg_clv"] = profile_cfg.get("avg_clv", "")
    return opportunity


def grade_completed_decision_log(game_pk, label, final_score):
    if not ENABLE_DECISION_LOG:
        return
    final_total = final_total_from_score(final_score)
    if final_total is None:
        return
    rows = csv_read_rows(DECISION_LOG_FILE)
    if not rows:
        return

    changed = False
    for row in rows:
        same_game = (
            row.get("date") == today()
            and (
                row.get("game_pk") == str(game_pk)
                or row.get("game") == label
                or row.get("game_key") == f"{today()}::{label}"
            )
        )
        if not same_game:
            continue
        if row.get("result") in ["WIN", "LOSS", "PUSH"]:
            continue
        side = str(row.get("side", "")).upper()
        line = safe_float(row.get("line"), None)
        if side not in ["OVER", "UNDER"] or line is None:
            continue
        result = grade_bet(side, line, final_total)
        row["final_score"] = final_score
        row["final_total"] = final_total
        row["result"] = result
        row["units"] = american_odds_profit_units(row.get("price"), result)
        close_line = safe_float(row.get("live_total"), None)
        # If no later close snapshot is available, leave CLV blank. Poll snapshots can enrich this later.
        if row.get("clv") in [None, ""]:
            row["clv"] = ""
        row["graded_at"] = now_local().isoformat()
        changed = True
    if changed:
        csv_write_rows(DECISION_LOG_FILE, decision_log_fieldnames(), rows)
        build_adaptive_config_from_results()
        print(f"DECISION LOG GRADED | {label} | Final {final_score} | adaptive config refreshed")


def decision_report_lines(report_date=None):
    report_date = report_date or today()
    rows = [r for r in csv_read_rows(DECISION_LOG_FILE) if r.get("date") == report_date]
    if not rows:
        return ["Decision Database: no rows logged yet."]
    lines = ["Decision Database:"]
    for action in ["BET_NOW", "TEST_UNIT", "RESEARCH_ONLY", "NO_BET"]:
        bucket = [r for r in rows if r.get("action") == action and r.get("result") in ["WIN", "LOSS", "PUSH"]]
        pending = [r for r in rows if r.get("action") == action and r.get("result") not in ["WIN", "LOSS", "PUSH"]]
        if bucket:
            w, l, p, pct, units = summarize_record(bucket)
            lines.append(f"• {action}: {w}-{l}-{p} | {pct}% | {units:+.2f}u | pending {len(pending)}")
        elif pending:
            lines.append(f"• {action}: {len(pending)} pending")
    # Show pass value only for graded NO_BET rows: negative units here means avoiding those bets saved money.
    passed = [r for r in rows if r.get("action") == "NO_BET" and r.get("result") in ["WIN", "LOSS", "PUSH"]]
    if passed:
        w, l, p, pct, units = summarize_record(passed)
        lines.append(f"• Passed-play audit: would-have been {w}-{l}-{p} | {units:+.2f}u")
    return lines


def adaptive_report_lines():
    if not ENABLE_ADAPTIVE_REPORTING:
        return []
    config = build_adaptive_config_from_results()
    lines = ["Adaptive Profile Config:"]
    if not config:
        lines.append(f"• Building samples. Need {MIN_ADAPTIVE_SAMPLE}+ graded BET_NOW/TEST_UNIT decisions per profile.")
        return lines
    for profile, cfg in sorted(config.items(), key=lambda kv: safe_int(kv[1].get("sample"), 0), reverse=True):
        lines.append(
            f"• {profile}: {cfg.get('status')} | Sample {cfg.get('sample')} | "
            f"ROI {safe_float(cfg.get('roi'), 0):+.2%} | CLV {safe_float(cfg.get('avg_clv'), 0):+.2f} | "
            f"ConfAdj {cfg.get('confidence_adjustment')}"
        )
    return lines

def game_key_from_info(info):
    return f"{today()}::{info.get('away')} at {info.get('home')}"


def score_text(info):
    return f"{info.get('away_runs', 0)}-{info.get('home_runs', 0)}"


def final_total_from_score(score):
    try:
        parts = str(score).replace(" ", "").split("-")
        if len(parts) != 2:
            return None
        return int(parts[0]) + int(parts[1])
    except Exception:
        return None


def schedule_final_score(game):
    try:
        away = game.get("teams", {}).get("away", {}).get("score")
        home = game.get("teams", {}).get("home", {}).get("score")
        if away is None or home is None:
            return ""
        return f"{away}-{home}"
    except Exception:
        return ""


def grade_bet(side, line, final_total):
    line = safe_float(line, None)
    final_total = safe_float(final_total, None)
    if line is None or final_total is None:
        return "UNKNOWN"
    side = str(side or "").upper()
    if final_total == line:
        return "PUSH"
    if side == "OVER":
        return "WIN" if final_total > line else "LOSS"
    if side == "UNDER":
        return "WIN" if final_total < line else "LOSS"
    return "UNKNOWN"


def pattern_tags_from_row(row):
    tags = []
    side = str(row.get("side", "")).upper()
    if side:
        tags.append(side)

    inning = safe_int(row.get("inning"), 0)
    if inning:
        if inning <= 3:
            tags.append("EARLY")
        elif inning <= 6:
            tags.append("MID")
        else:
            tags.append("LATE")

    base_out = str(row.get("base_out", "")).lower()
    if "bases loaded" in base_out:
        tags.append("BASES_LOADED")
    elif "1st and 3rd" in base_out or "2nd and 3rd" in base_out:
        tags.append("MULTI_RUNNERS_SCORING")
    elif "runner on 2nd" in base_out or "runner on 3rd" in base_out:
        tags.append("SCORING_POSITION")
    elif "bases empty" in base_out:
        tags.append("BASES_EMPTY")

    outs = safe_int(row.get("outs"), -1)
    if outs == 0:
        tags.append("NO_OUTS")
    elif outs == 1:
        tags.append("ONE_OUT")
    elif outs == 2:
        tags.append("TWO_OUTS")

    stress = safe_int(row.get("stress"), 0)
    contact = safe_int(row.get("contact"), 0)
    p2r = safe_int(row.get("p2r"), 0)
    conv = safe_int(row.get("conv"), 0)
    prev = safe_int(row.get("prev"), 0)
    pred = safe_int(row.get("pred_move"), 0)
    threat = safe_int(row.get("threat_index"), 0)
    stack = safe_int(row.get("signal_stack"), 0)
    lag = safe_int(row.get("market_lag"), 0)

    if stress >= 80:
        tags.append("STRESS_80_PLUS")
    elif stress >= 65:
        tags.append("STRESS_65_PLUS")

    if contact >= 70:
        tags.append("CONTACT_70_PLUS")
    elif contact >= 55:
        tags.append("CONTACT_55_PLUS")

    if p2r >= 85:
        tags.append("P2R_85_PLUS")
    elif p2r >= 70:
        tags.append("P2R_70_PLUS")

    if conv >= 80:
        tags.append("CONV_80_PLUS")
    elif conv >= 65:
        tags.append("CONV_65_PLUS")

    if prev >= 75:
        tags.append("PREV_75_PLUS")
    elif prev >= 60:
        tags.append("PREV_60_PLUS")

    if pred >= 75:
        tags.append("PRED_75_PLUS")

    if threat >= 85:
        tags.append("THREAT_85_PLUS")
    elif threat >= 70:
        tags.append("THREAT_70_PLUS")

    if stack >= 5:
        tags.append("STACK_5_PLUS")
    elif stack >= 4:
        tags.append("STACK_4_PLUS")

    if lag >= 80:
        tags.append("LAG_80_PLUS")
    elif lag >= 60:
        tags.append("LAG_60_PLUS")

    k_env = safe_int(row.get("k_env"), 0)
    bullpen_lockdown = safe_int(row.get("bullpen_lockdown"), 0)
    traffic_conversion = safe_int(row.get("traffic_conversion"), 0)
    hh_eff = safe_int(row.get("hh_eff"), 0)
    hh_under = safe_int(row.get("hh_under"), 0)

    if k_env >= 75:
        tags.append("K_ENV_75_PLUS")
    elif k_env >= 60:
        tags.append("K_ENV_60_PLUS")

    if bullpen_lockdown >= 75:
        tags.append("BULLPEN_LOCK_75_PLUS")
    elif bullpen_lockdown >= 55:
        tags.append("BULLPEN_LOCK_55_PLUS")

    if traffic_conversion >= 70:
        tags.append("TRAFFIC_CONV_70_PLUS")
    elif traffic_conversion >= 55:
        tags.append("TRAFFIC_CONV_55_PLUS")

    if hh_eff >= 70:
        tags.append("HH_EFF_70_PLUS")
    elif hh_eff >= 55:
        tags.append("HH_EFF_55_PLUS")

    if hh_under >= 70:
        tags.append("HH_UNDER_70_PLUS")
    elif hh_under >= 55:
        tags.append("HH_UNDER_55_PLUS")

    profile = row.get("market_reaction_profile") or row.get("profile")
    if profile:
        tags.append(f"PROFILE_{profile}")
    tier = row.get("calculated_risk_tier")
    if tier:
        tags.append(f"TIER_{tier}")

    return tags



def market_reaction_profile_from_scores(scores, scenario=""):
    """Stable profile label for reports, learning, and calculated-risk tiers."""
    scores = scores or {}
    profile = scores.get("market_reaction_profile") or ""
    if profile:
        return profile
    scenario = str(scenario or "").upper()
    if "DISCOUNTED" in scenario and "OVER" in scenario:
        return "DISCOUNTED_OVER"
    if "CONTINUATION" in scenario and "OVER" in scenario:
        return "CONTINUATION_OVER"
    if "INFLATED" in scenario and "UNDER" in scenario:
        return "INFLATED_UNDER"
    if "FALSE" in scenario and "INFLATION" in scenario:
        return "FALSE_INFLATION_FADE"
    if "PITCHING" in scenario and "UNDER" in scenario:
        return "PITCHING_DOMINANCE_UNDER"
    return "UNCLASSIFIED"


def profile_learning_fieldnames():
    return [
        "profile", "side", "sample", "wins", "losses", "pushes", "win_pct",
        "units", "avg_clv", "profile_status", "confidence_adjustment", "updated_at",
    ]


def profile_near_miss_fieldnames():
    return [
        "timestamp", "date", "game", "game_pk", "profile", "side", "line", "price",
        "reason", "confidence", "edge", "inning", "score", "base_out",
        "settle_down_score", "continuation_score", "false_inflation_score",
        "discounted_over_score", "pitching_dominance_under_score",
        "market_reaction_move", "market_confirmation_score", "expected_value",
        "near_miss_final_total", "near_miss_result", "near_miss_graded_at",
    ]


def profile_stats_from_rows(profile, side=None):
    """Historical profile record from graded_results.csv. Small sample = no hard adjustment."""
    rows = [r for r in csv_read_rows(GRADED_RESULTS_FILE) if r.get("result") in ["WIN", "LOSS", "PUSH"]]
    matched = []
    for r in rows:
        row_profile = r.get("market_reaction_profile") or r.get("profile") or "UNCLASSIFIED"
        if row_profile != profile:
            continue
        if side and str(r.get("side", "")).upper() != str(side).upper():
            continue
        matched.append(r)
    w, l, p, pct, units = summarize_record(matched) if matched else (0, 0, 0, 0, 0.0)
    clvs = [safe_float(r.get("clv"), 0) for r in matched if str(r.get("clv", "")).strip() != ""]
    avg_clv = round(avg(clvs), 2) if clvs else 0.0
    sample = w + l + p
    if sample < PROFILE_MIN_SAMPLE_TO_TIGHTEN:
        status = "OPEN_TEST"
        conf_adj = 0
    elif pct < PROFILE_WEAK_WIN_PCT or avg_clv < -PROFILE_TARGET_CLV:
        status = "TIGHTEN"
        conf_adj = PROFILE_TIGHTEN_CONF_BUMP
    elif pct >= PROFILE_STRONG_WIN_PCT and avg_clv >= PROFILE_TARGET_CLV:
        status = "PROVEN"
        conf_adj = -PROFILE_LOOSEN_CONF_CREDIT
    else:
        status = "HOLD"
        conf_adj = 0
    return {
        "profile": profile, "side": side or "", "sample": sample,
        "wins": w, "losses": l, "pushes": p, "win_pct": pct,
        "units": units, "avg_clv": avg_clv, "profile_status": status,
        "confidence_adjustment": conf_adj, "updated_at": now_local().isoformat(),
    }


def profile_learning_adjustment(profile, side=None):
    if not ENABLE_PROFILE_LEARNING_GATES or not profile:
        return {"profile_status": "DISABLED", "confidence_adjustment": 0, "sample": 0, "win_pct": 0, "avg_clv": 0}
    return profile_stats_from_rows(profile, side)


def calculated_risk_tier(opportunity, market_context=None):
    """Tier every alert so we can take risk now but learn/tighten by bucket later."""
    if not opportunity:
        return "NONE"
    scores = opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    conf = safe_int(opportunity.get("confidence"), 0)
    edge = abs(safe_float(opportunity.get("edge"), 0))
    ev = safe_float(opportunity.get("expected_value"), 0)
    mconf = safe_int((market_context or {}).get("market_confirmation_score"), safe_int(opportunity.get("market_confirmation_score"), 0))
    profile_boost = 0
    strength = profile_strength_score(profile, scores)
    if profile == "DISCOUNTED_OVER" and safe_int(scores.get("discounted_over_score"), 0) >= 75:
        profile_boost += 4
    if profile == "CONTINUATION_OVER" and strength >= 80:
        profile_boost += 3
    if profile in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"] and safe_int(scores.get("settle_down_score"), 0) >= 70:
        profile_boost += 4
    if profile == "PITCHING_DOMINANCE_UNDER" and safe_int(scores.get("pitching_dominance_under_score"), 0) >= 76:
        profile_boost += 4
    if ENABLE_PROFILE_STRENGTH_TIERING:
        if strength >= PROFILE_STRENGTH_TIER_A:
            profile_boost += 3
        elif strength >= PROFILE_STRENGTH_TIER_B:
            profile_boost += 1
    hist = historical_profile_tier_adjustment(profile, opportunity.get("side"))
    profile_boost += hist
    tier_score = conf + profile_boost
    if is_discounted_over_tier_a({"inning": safe_int(scores.get("inning"), 0)}, opportunity, scores):
        return "A"
    if tier_score >= CALCULATED_RISK_TIER_A_CONF and edge >= 2.0 and (ev >= 0.02 or mconf >= 72):
        return "A"
    if tier_score >= CALCULATED_RISK_TIER_B_CONF and edge >= 1.0 and (ev >= 0 or mconf >= 60):
        return "B"
    if tier_score >= CALCULATED_RISK_TIER_C_CONF:
        return "C"
    return "WATCH_ONLY"


def tier_unit_guidance(tier):
    """Suggested staking label only. No daily caps; every qualified game can still fire."""
    if not ENABLE_TIER_UNIT_GUIDANCE:
        return ""
    t = str(tier or "").upper()
    if t == "A":
        return TIER_A_UNIT_LABEL
    if t == "B":
        return TIER_B_UNIT_LABEL
    if t == "C":
        return TIER_C_UNIT_LABEL
    return TIER_WATCH_UNIT_LABEL




def historical_profile_tier_adjustment(profile, side=None):
    """Small sample-disciplined boost/penalty for profile tiering only.
    It does not change raw projections or force alerts; it just helps strong
    profiles earn Tier A/B status once they have evidence.
    """
    if not ENABLE_PROFILE_STRENGTH_TIERING or not profile:
        return 0
    try:
        stats = profile_stats_from_rows(profile, side)
    except Exception:
        return 0
    sample = safe_int(stats.get("sample"), 0)
    win_pct = safe_float(stats.get("win_pct"), 0)
    units = safe_float(stats.get("units"), 0)
    if sample < PROFILE_HISTORY_BOOST_MIN_SAMPLE:
        return 0
    if win_pct >= PROFILE_HISTORY_STRONG_WIN_PCT and units > 0:
        return PROFILE_HISTORY_STRONG_BOOST
    if win_pct < PROFILE_HISTORY_WEAK_WIN_PCT or units < 0:
        return -PROFILE_HISTORY_WEAK_PENALTY
    return 0



def discounted_over_window(info):
    """Simple inning bucket for Discounted OVER reporting."""
    inning = safe_int((info or {}).get("inning"), 0)
    if inning <= 3:
        return "EARLY_1_3"
    if inning <= 6:
        return "MIDDLE_4_6"
    return "LATE_7_PLUS"


def should_block_late_discounted_over(info, opportunity, scores=None):
    """
    V3.7.9: protect the best profile from weak late-game versions.
    Discounted OVER stays aggressive early/mid, but late innings need immediate
    traffic/threat quality because time is running out.
    """
    if not ENABLE_DISCOUNTED_OVER_LATE_DISCIPLINE:
        return False, "late discipline disabled"
    info = info or {}
    opportunity = opportunity or {}
    scores = scores or opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    if profile != "DISCOUNTED_OVER" or str(opportunity.get("side", "")).upper() != "OVER":
        return False, "not Discounted OVER"
    inning = safe_int(info.get("inning"), safe_int(scores.get("inning"), 0))
    if inning < DISCOUNTED_OVER_LATE_INNING:
        return False, "not late"
    edge = abs(safe_float(opportunity.get("edge"), 0))
    risk = master_risk_filter_score(opportunity, info, scores)
    threat = safe_int(scores.get("threat_index"), 0)
    traffic = safe_int(scores.get("traffic_conversion"), 0)
    p2r = safe_int(scores.get("pressure_to_runs"), 0)
    conv = safe_int(scores.get("run_conversion"), 0)
    if risk <= DISCOUNTED_OVER_LATE_MAX_RISK and edge >= DISCOUNTED_OVER_LATE_MIN_EDGE:
        return False, "late edge/risk acceptable"
    elite_live = (
        threat >= DISCOUNTED_OVER_LATE_MIN_THREAT
        and traffic >= DISCOUNTED_OVER_LATE_MIN_TRAFFIC
        and p2r >= DISCOUNTED_OVER_LATE_MIN_P2R
        and conv >= DISCOUNTED_OVER_LATE_MIN_CONV
    )
    if elite_live:
        return False, "late live traffic elite"
    return True, (
        f"late Discounted OVER watch-only: inning={inning} edge={edge:.1f} risk={risk} "
        f"threat={threat} traffic={traffic} p2r={p2r} conv={conv}"
    )


def is_discounted_over_tier_a(info, opportunity, scores=None):
    """V3.7.9: elite Discounted OVER deserves Tier A; normal ones remain Tier B/C."""
    if not ENABLE_DISCOUNTED_OVER_TIER_A:
        return False
    info = info or {}
    opportunity = opportunity or {}
    scores = scores or opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    if profile != "DISCOUNTED_OVER" or str(opportunity.get("side", "")).upper() != "OVER":
        return False
    inning = safe_int(info.get("inning"), safe_int(scores.get("inning"), 0))
    score = safe_int(scores.get("discounted_over_score"), 0)
    value = master_market_value_score(opportunity, scores)
    risk = master_risk_filter_score(opportunity, info, scores)
    edge = abs(safe_float(opportunity.get("edge"), 0))
    return (
        inning <= DISCOUNTED_OVER_TIER_A_MAX_INNING
        and score >= DISCOUNTED_OVER_TIER_A_SCORE
        and value >= DISCOUNTED_OVER_TIER_A_VALUE
        and risk <= DISCOUNTED_OVER_TIER_A_MAX_RISK
        and edge >= DISCOUNTED_OVER_TIER_A_EDGE
    )


def profile_promotion_min_confidence(profile):
    """Profile-specific BET NOW confidence floor. Lower is more aggressive."""
    p = str(profile or "").upper()
    if p == "DISCOUNTED_OVER":
        return PROFILE_PROMOTE_DISCOUNTED_OVER_CONF
    if p == "CONTINUATION_OVER":
        return PROFILE_PROMOTE_CONTINUATION_OVER_CONF
    if p == "INFLATED_UNDER":
        return PROFILE_PROMOTE_INFLATED_UNDER_CONF
    if p == "FALSE_INFLATION_FADE":
        return PROFILE_PROMOTE_FALSE_INFLATION_CONF
    if p == "PITCHING_DOMINANCE_UNDER":
        return PROFILE_PROMOTE_PITCHING_DOM_UNDER_CONF
    return V26_MIN_BETNOW_CONFIDENCE


def profile_strength_score(profile, scores):
    """Primary profile-strength number used in V3.7.6 promotion logic."""
    scores = scores or {}
    p = str(profile or "").upper()
    if p == "DISCOUNTED_OVER":
        return safe_int(scores.get("discounted_over_score"), 0)
    if p == "CONTINUATION_OVER":
        cont = safe_int(scores.get("continuation_score"), 0)
        exhaustion = safe_int(scores.get("continuation_exhaustion_score"), 0)
        # High exhaustion means the market may already be done reacting.
        # Keep the profile alive, but reduce profile strength unless live
        # evidence is truly elite.
        penalty = max(0, exhaustion - 50) * 0.35
        return round(clamp(cont - penalty))
    if p == "INFLATED_UNDER":
        return safe_int(scores.get("settle_down_score"), 0)
    if p == "FALSE_INFLATION_FADE":
        return safe_int(scores.get("false_inflation_score"), 0)
    if p == "PITCHING_DOMINANCE_UNDER":
        return safe_int(scores.get("pitching_dominance_under_score"), 0)
    return 0


def profile_promotion_reason(info, opportunity, market_context=None):
    """
    V3.7.6: decide whether a classified market-reaction profile should be
    promoted to BET NOW even if the older generic action engine would only WATCH.
    This does not remove app/price/risk/final-gate protection. It simply lets
    strong profiles compete using their own thresholds.
    """
    if not ENABLE_PROFILE_PROMOTION_LOGIC or not opportunity:
        return False, "profile promotion disabled"

    scores = opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    side = str(opportunity.get("side", "")).upper()
    confidence = safe_int(opportunity.get("confidence"), 0)
    edge = safe_float(opportunity.get("edge"), 0)
    abs_edge = abs(edge)
    value = master_market_value_score(opportunity, scores)
    risk = master_risk_filter_score(opportunity, info or {}, scores)
    age = safe_float((market_context or {}).get("best_line_age_seconds"), None)
    move = safe_float(scores.get("market_reaction_move"), 0)
    p2r = safe_int(scores.get("pressure_to_runs"), 0)
    conv = safe_int(scores.get("run_conversion"), 0)
    traffic = safe_int(scores.get("traffic_conversion"), 0)
    pressure = safe_int(scores.get("current_inning_pressure"), 0)
    contact = safe_int(scores.get("contact_quality"), 0)
    profile_score = profile_strength_score(profile, scores)
    exhaustion = safe_int(scores.get("continuation_exhaustion_score"), 0)
    min_conf = profile_promotion_min_confidence(profile)

    if profile in ["UNCLASSIFIED", "NEUTRAL_MARKET", "INFLATED_NO_BET", "DISCOUNTED_NO_BET", ""]:
        return False, f"profile {profile} is not promotion eligible"
    if age is not None and age > PROFILE_PROMOTE_MAX_LINE_AGE:
        return False, f"profile promotion blocked: line age {int(age)}s > {PROFILE_PROMOTE_MAX_LINE_AGE}s"
    if confidence < min_conf:
        return False, f"profile promotion blocked: confidence {confidence} < {min_conf} for {profile}"
    if abs_edge < PROFILE_PROMOTE_MIN_EDGE:
        return False, f"profile promotion blocked: edge {abs_edge:.1f} < {PROFILE_PROMOTE_MIN_EDGE:.1f}"
    if value < PROFILE_PROMOTE_MIN_VALUE:
        return False, f"profile promotion blocked: value {value} < {PROFILE_PROMOTE_MIN_VALUE}"
    if risk > PROFILE_PROMOTE_MAX_RISK:
        return False, f"profile promotion blocked: risk {risk} > {PROFILE_PROMOTE_MAX_RISK}"

    if profile == "DISCOUNTED_OVER":
        late_block, late_reason = should_block_late_discounted_over(info or {}, opportunity, scores)
        if late_block:
            return False, late_reason
        ok = side == "OVER" and profile_score >= PROFILE_PROMOTE_DISCOUNTED_MIN_SCORE and edge > 0
        tier_a_note = " | TierA-eligible" if is_discounted_over_tier_a(info or {}, opportunity, scores) else ""
        return ok, f"DISCOUNTED_OVER promotion score={profile_score} value={value} risk={risk}{tier_a_note}"

    if profile == "CONTINUATION_OVER":
        base_ok = (
            side == "OVER"
            and profile_score >= PROFILE_PROMOTE_CONTINUATION_MIN_SCORE
            and edge > 0
            and p2r >= PROFILE_PROMOTE_CONTINUATION_MIN_P2R
            and conv >= PROFILE_PROMOTE_CONTINUATION_MIN_CONV
            and pressure >= 25
        )
        exhaustion_ok = True
        exhaustion_note = ""
        if ENABLE_CONTINUATION_EXHAUSTION:
            if exhaustion >= CONT_EXHAUSTION_DANGER_SCORE or move >= CONT_EXHAUSTION_MOVE_DANGER:
                exhaustion_ok = (
                    p2r >= CONT_EXHAUSTION_DANGER_MIN_P2R
                    and conv >= CONT_EXHAUSTION_DANGER_MIN_CONV
                    and pressure >= CONT_EXHAUSTION_DANGER_MIN_PRESSURE
                    and abs_edge >= CONT_EXHAUSTION_DANGER_MIN_EDGE
                    and risk <= CONT_EXHAUSTION_DANGER_MAX_RISK
                )
                exhaustion_note = (
                    f" danger_exhaustion={exhaustion} requires elite p2r/conv/pressure/edge/risk"
                )
            elif exhaustion >= CONT_EXHAUSTION_CAUTION_SCORE or move >= CONT_EXHAUSTION_MOVE_CAUTION:
                exhaustion_ok = (
                    p2r >= CONT_EXHAUSTION_CAUTION_MIN_P2R
                    and conv >= CONT_EXHAUSTION_CAUTION_MIN_CONV
                    and pressure >= CONT_EXHAUSTION_CAUTION_MIN_PRESSURE
                )
                exhaustion_note = (
                    f" caution_exhaustion={exhaustion} requires stronger p2r/conv/pressure"
                )
        ok = base_ok and exhaustion_ok
        return ok, (
            f"CONTINUATION_OVER promotion cont={profile_score} exhaust={exhaustion} "
            f"move={move:+.1f} p2r={p2r} conv={conv} pressure={pressure} "
            f"value={value} risk={risk}{exhaustion_note}"
        )

    if profile == "INFLATED_UNDER":
        ok = (
            side == "UNDER"
            and move >= INFLATED_TOTAL_MOVE_RUNS
            and profile_score >= PROFILE_PROMOTE_INFLATED_MIN_SETTLE
            and edge < 0
            and pressure <= INFLATED_UNDER_ALLOW_CURRENT_PRESSURE + 8
            and contact <= INFLATED_UNDER_ALLOW_CONTACT + 8
            and traffic <= INFLATED_UNDER_MAX_TRAFFIC + 6
        )
        return ok, f"INFLATED_UNDER promotion settle={profile_score} move={move:+.1f} pressure={pressure} contact={contact} traffic={traffic}"

    if profile == "FALSE_INFLATION_FADE":
        ok = (
            side == "UNDER"
            and move >= INFLATED_TOTAL_MOVE_RUNS
            and profile_score >= PROFILE_PROMOTE_FALSE_MIN_SCORE
            and edge < 0
            and p2r <= INFLATED_UNDER_MAX_P2R_SOFT
        )
        return ok, f"FALSE_INFLATION_FADE promotion false={profile_score} move={move:+.1f} p2r={p2r}"

    if profile == "PITCHING_DOMINANCE_UNDER":
        ok = side == "UNDER" and profile_score >= PROFILE_PROMOTE_PITCHING_MIN_SCORE and edge < 0
        return ok, f"PITCHING_DOMINANCE_UNDER promotion pd={profile_score} value={value} risk={risk}"

    return False, f"profile {profile} did not meet promotion rules"


def under_profile_test_alert_reason(info, opportunity, scores):
    """
    V3.8.0: Controlled test-alert gate for rare UNDER profiles.
    This intentionally promotes qualifying INFLATED_UNDER and PITCHING_DOMINANCE_UNDER
    as Tier C / TEST UNIT so the system can collect real graded samples.
    """
    if not ENABLE_UNDER_PROFILE_TEST_ALERTS or not opportunity:
        return False, "under profile test alerts disabled"

    scores = scores or opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    side = str(opportunity.get("side", "")).upper()
    if side != "UNDER":
        return False, "under test alert requires UNDER side"

    edge = safe_float(opportunity.get("edge"), 0)
    abs_edge = abs(edge)
    risk = master_risk_filter_score(opportunity, info or {}, scores)
    move = safe_float(scores.get("market_reaction_move"), 0)
    settle = safe_int(scores.get("settle_down_score"), 0)
    cont = safe_int(scores.get("continuation_score"), 0)
    pressure = safe_int(scores.get("current_inning_pressure"), 0)
    traffic = safe_int(scores.get("traffic_conversion"), 0)
    contact = safe_int(scores.get("contact_quality"), 50)
    stress = safe_int(scores.get("pitcher_stress"), 0)
    p2r = safe_int(scores.get("pressure_to_runs"), 0)
    inning = safe_int((info or {}).get("inning"), 0)
    runs = safe_int((info or {}).get("total_runs"), 0)
    ref = safe_float(scores.get("market_reaction_reference"), None)
    live = safe_float(opportunity.get("line"), None)
    live_drop = safe_float(scores.get("pitching_dominance_live_drop"), 0)

    if profile in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"]:
        ok = (
            edge < 0
            and abs_edge >= INFLATED_UNDER_TEST_MIN_EDGE
            and move >= INFLATED_UNDER_TEST_MIN_MOVE
            and settle >= INFLATED_UNDER_TEST_MIN_SETTLE
            and cont <= INFLATED_UNDER_TEST_MAX_CONTINUATION
            and risk <= INFLATED_UNDER_TEST_MAX_RISK
            and pressure <= INFLATED_UNDER_TEST_MAX_PRESSURE
            and traffic <= INFLATED_UNDER_TEST_MAX_TRAFFIC
        )
        return ok, (
            f"{profile} TEST ALERT settle={settle} cont={cont} move={move:+.1f} "
            f"edge={abs_edge:.1f} risk={risk} pressure={pressure} traffic={traffic}"
        )

    if profile == "PITCHING_DOMINANCE_UNDER":
        pd_score = safe_int(scores.get("pitching_dominance_under_score"), 0)
        if live_drop == 0 and ref is not None and live is not None:
            live_drop = round(ref - live, 1)
        ok = (
            edge < 0
            and abs_edge >= PITCHING_DOMINANCE_TEST_MIN_EDGE
            and pd_score >= PITCHING_DOMINANCE_TEST_MIN_SCORE
            and (ref is None or ref <= PITCHING_DOMINANCE_TEST_OPEN_MAX)
            and PITCHING_DOMINANCE_TEST_MIN_INNING <= inning <= PITCHING_DOMINANCE_TEST_MAX_INNING
            and runs <= PITCHING_DOMINANCE_TEST_MAX_RUNS
            and live_drop <= PITCHING_DOMINANCE_TEST_MAX_LIVE_DROP
            and contact <= PITCHING_DOMINANCE_TEST_MAX_CONTACT
            and stress <= PITCHING_DOMINANCE_TEST_MAX_STRESS
            and p2r <= PITCHING_DOMINANCE_TEST_MAX_P2R
            and risk <= PROFILE_PROMOTE_MAX_RISK
        )
        return ok, (
            f"PITCHING_DOMINANCE_UNDER TEST ALERT pd={pd_score} inning={inning} runs={runs} "
            f"drop={live_drop:.1f} contact={contact} stress={stress} p2r={p2r} risk={risk}"
        )

    return False, f"profile {profile} is not under-test eligible"

def actual_entry_fieldnames():
    return [
        "timestamp", "date", "game", "game_pk", "strike_id",
        "profile", "tier", "bot_side", "bot_line", "bot_price", "bot_book",
        "actual_side", "actual_line", "actual_price", "actual_book",
        "final_total", "bot_result", "actual_result", "graded_at", "notes",
    ]


def actual_entry_template_from_strike(row):
    """Return a blank manual-entry row the user can fill later if they beat the bot number."""
    if not ENABLE_ACTUAL_ENTRY_TRACKING:
        return None
    return {
        "timestamp": now_local().isoformat(),
        "date": row.get("date") or today(),
        "game": row.get("game"),
        "game_pk": row.get("game_pk"),
        "strike_id": row.get("strike_id"),
        "profile": row.get("market_reaction_profile"),
        "tier": row.get("calculated_risk_tier"),
        "bot_side": row.get("side"),
        "bot_line": row.get("line"),
        "bot_price": row.get("price"),
        "bot_book": row.get("recommended_book"),
        "actual_side": "",
        "actual_line": "",
        "actual_price": "",
        "actual_book": "",
        "final_total": "",
        "bot_result": "",
        "actual_result": "",
        "graded_at": "",
        "notes": "Fill actual fields when user entered a better/different number.",
    }


def log_profile_near_miss(info, opportunity, reason, market_context=None):
    """Logs calculated-risk candidates that were close but rejected; no SMS spam."""
    if not ENABLE_PROFILE_NEAR_MISS_LOG or not opportunity:
        return
    scores = opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    if profile in ["UNCLASSIFIED", "NEUTRAL_MARKET", "DISCOUNTED_NO_BET"]:
        return
    row = {
        "timestamp": now_local().isoformat(),
        "date": today(),
        "game": f"{info.get('away')} at {info.get('home')}",
        "game_pk": info.get("game_pk", ""),
        "profile": profile,
        "side": opportunity.get("side"),
        "line": opportunity.get("line"),
        "price": opportunity.get("price"),
        "reason": reason,
        "confidence": opportunity.get("confidence"),
        "edge": opportunity.get("edge"),
        "inning": info.get("inning"),
        "score": score_text(info),
        "base_out": f"{(info.get('base_state') or {}).get('label', '')}, {info.get('outs')} out(s)",
        "settle_down_score": scores.get("settle_down_score"),
        "continuation_score": scores.get("continuation_score"),
        "false_inflation_score": scores.get("false_inflation_score"),
        "discounted_over_score": scores.get("discounted_over_score"),
        "pitching_dominance_under_score": scores.get("pitching_dominance_under_score"),
        "market_reaction_move": scores.get("market_reaction_move"),
        "market_confirmation_score": (market_context or {}).get("market_confirmation_score"),
        "expected_value": opportunity.get("expected_value"),
        "near_miss_final_total": "",
        "near_miss_result": "PENDING",
        "near_miss_graded_at": "",
    }
    csv_append_once(PROFILE_NEAR_MISS_FILE, profile_near_miss_fieldnames(), row)
    print(f"PROFILE NEAR MISS | {row['game']} | {profile} | {row['side']} {row['line']} | {reason}")


def profile_research_fieldnames():
    return [
        "research_key", "timestamp", "date", "game", "game_pk", "profile", "side", "line", "price",
        "research_bucket", "reason", "promoted", "action", "confidence", "edge", "inning", "inning_state",
        "score", "base_out", "opening_total", "live_total", "projected_total", "market_reaction_move",
        "settle_down_score", "continuation_score", "continuation_exhaustion_score", "false_inflation_score",
        "discounted_over_score", "pitching_dominance_under_score", "p2r", "conv", "traffic",
        "contact", "threat", "stress", "bullpen_risk", "risk_score", "value_score", "market_confirmation_score",
        "expected_value", "final_score", "final_total", "would_have_result", "graded_at",
    ]


def profile_research_append_once(row):
    """Append research row once per date/game/profile/side/line/inning bucket."""
    if not ENABLE_PROFILE_RESEARCH_DATABASE:
        return
    key = row.get("research_key")
    if key:
        existing = csv_read_rows(PROFILE_RESEARCH_FILE)
        if any(r.get("research_key") == key for r in existing):
            return
    csv_append_once(PROFILE_RESEARCH_FILE, profile_research_fieldnames(), row)


def log_profile_research_candidate(info, opportunity, reason, market_context=None, promoted=False):
    """
    V3.7.9 research database. Logs candidates and near-candidates so rare profiles
    can be studied even without SMS. No extra API calls and no SMS spam.
    """
    if not ENABLE_PROFILE_RESEARCH_DATABASE or not opportunity:
        return
    scores = opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    if profile in ["UNCLASSIFIED", "NEUTRAL_MARKET", "DISCOUNTED_NO_BET", "INFLATED_NO_BET"]:
        return
    side = str(opportunity.get("side", "")).upper()
    if profile == "DISCOUNTED_OVER" and not PROFILE_RESEARCH_LOG_DISCOUNTED_OVER:
        return
    if profile in ["INFLATED_UNDER", "FALSE_INFLATION_FADE", "PITCHING_DOMINANCE_UNDER"] and not PROFILE_RESEARCH_LOG_UNDER_CANDIDATES:
        return
    if profile not in ["DISCOUNTED_OVER", "INFLATED_UNDER", "FALSE_INFLATION_FADE", "PITCHING_DOMINANCE_UNDER"]:
        return
    inning = safe_int(info.get("inning"), 0)
    bucket = discounted_over_window(info) if profile == "DISCOUNTED_OVER" else profile
    game = f"{info.get('away')} at {info.get('home')}"
    line = opportunity.get("line")
    research_key = f"{today()}|{info.get('game_pk','')}|{profile}|{side}|{line}|{inning}|{bucket}"
    row = {
        "research_key": research_key,
        "timestamp": now_local().isoformat(),
        "date": today(),
        "game": game,
        "game_pk": info.get("game_pk", ""),
        "profile": profile,
        "side": side,
        "line": line,
        "price": opportunity.get("price"),
        "research_bucket": bucket,
        "reason": reason,
        "promoted": "Y" if promoted else "N",
        "action": opportunity.get("action", "CANDIDATE"),
        "confidence": opportunity.get("confidence"),
        "edge": opportunity.get("edge"),
        "inning": info.get("inning"),
        "inning_state": info.get("inning_state"),
        "score": score_text(info),
        "base_out": f"{(info.get('base_state') or {}).get('label', '')}, {info.get('outs')} out(s)",
        "opening_total": (market_context or {}).get("opening_total"),
        "live_total": (market_context or {}).get("live_total", opportunity.get("line")),
        "projected_total": opportunity.get("projected_total") or opportunity.get("projection"),
        "market_reaction_move": scores.get("market_reaction_move"),
        "settle_down_score": scores.get("settle_down_score"),
        "continuation_score": scores.get("continuation_score"),
        "continuation_exhaustion_score": scores.get("continuation_exhaustion_score"),
        "false_inflation_score": scores.get("false_inflation_score"),
        "discounted_over_score": scores.get("discounted_over_score"),
        "pitching_dominance_under_score": scores.get("pitching_dominance_under_score"),
        "p2r": scores.get("pressure_to_runs"),
        "conv": scores.get("run_conversion"),
        "traffic": scores.get("traffic_conversion"),
        "contact": scores.get("contact_quality"),
        "threat": scores.get("threat_index"),
        "stress": scores.get("pitcher_stress"),
        "bullpen_risk": scores.get("bullpen_risk"),
        "risk_score": master_risk_filter_score(opportunity, info, scores),
        "value_score": master_market_value_score(opportunity, scores),
        "market_confirmation_score": (market_context or {}).get("market_confirmation_score"),
        "expected_value": opportunity.get("expected_value"),
        "final_score": "",
        "final_total": "",
        "would_have_result": "PENDING",
        "graded_at": "",
    }
    profile_research_append_once(row)
    print(f"PROFILE RESEARCH | {game} | {profile} | {side} {line} | {bucket} | {reason}")


def grade_profile_research_candidates(game_pk, label, final_score):
    """Grade research candidates when a game goes final, even if no SMS was sent."""
    if not ENABLE_PROFILE_RESEARCH_DATABASE:
        return
    final_total = final_total_from_score(final_score)
    if final_total is None:
        return
    rows = csv_read_rows(PROFILE_RESEARCH_FILE)
    if not rows:
        return
    changed = False
    for row in rows:
        same_game = (
            row.get("date") == today()
            and (row.get("game_pk") == str(game_pk) or row.get("game") == label)
        )
        if not same_game:
            continue
        if row.get("would_have_result") in ["WIN", "LOSS", "PUSH"]:
            continue
        result = grade_bet(row.get("side"), row.get("line"), final_total)
        row["final_score"] = final_score
        row["final_total"] = final_total
        row["would_have_result"] = result
        row["graded_at"] = now_local().isoformat()
        changed = True
    if changed:
        csv_write_rows(PROFILE_RESEARCH_FILE, profile_research_fieldnames(), rows)
        print(f"PROFILE RESEARCH GRADED | {label} | Final {final_score}")


def profile_research_summary_lines(report_date=None):
    report_date = report_date or today()
    rows = [r for r in csv_read_rows(PROFILE_RESEARCH_FILE) if r.get("date") == report_date]
    lines = []
    if not rows:
        lines.append("Profile Research: no candidates logged today.")
        return lines
    lines.append("Profile Research Candidates:")
    by_profile = {}
    for r in rows:
        by_profile.setdefault(r.get("profile") or "UNCLASSIFIED", []).append(r)
    for profile, items in sorted(by_profile.items()):
        graded = [r for r in items if r.get("would_have_result") in ["WIN", "LOSS", "PUSH"]]
        promoted = sum(1 for r in items if str(r.get("promoted", "")).upper() == "Y")
        if graded:
            wins = sum(1 for r in graded if r.get("would_have_result") == "WIN")
            losses = sum(1 for r in graded if r.get("would_have_result") == "LOSS")
            pushes = sum(1 for r in graded if r.get("would_have_result") == "PUSH")
            pct = round((wins / max(1, wins + losses)) * 100, 1) if (wins + losses) else 0
            lines.append(f"• {profile}: candidates {len(items)} | promoted {promoted} | would-have {wins}-{losses}-{pushes} | {pct}%")
        else:
            lines.append(f"• {profile}: candidates {len(items)} | promoted {promoted} | pending finals")
    # Discounted OVER sub-buckets reveal whether late versions need discipline.
    disc = [r for r in rows if r.get("profile") == "DISCOUNTED_OVER"]
    if disc:
        bucket_map = {}
        for r in disc:
            bucket_map.setdefault(r.get("research_bucket") or "UNKNOWN", []).append(r)
        parts = []
        for bucket, items in sorted(bucket_map.items()):
            graded = [r for r in items if r.get("would_have_result") in ["WIN", "LOSS", "PUSH"]]
            if graded:
                w = sum(1 for r in graded if r.get("would_have_result") == "WIN")
                l = sum(1 for r in graded if r.get("would_have_result") == "LOSS")
                p = sum(1 for r in graded if r.get("would_have_result") == "PUSH")
                parts.append(f"{bucket}:{w}-{l}-{p}")
            else:
                parts.append(f"{bucket}:{len(items)} pending")
        lines.append("Discounted OVER Windows: " + ", ".join(parts))
    return lines


def strike_fieldnames():
    return [
        "opportunity_id", "strike_id", "timestamp", "date", "game_key", "game_pk", "game",
        "side", "line", "price",
        "opening_total", "live_total", "projected_total", "edge", "edge_grade",
        "inning", "inning_state", "outs", "score", "base_out",
        "projection_score", "confirmation_score",
        "stress", "contact", "p2r", "conv", "prev", "pred_move",
        "threat_index", "signal_stack", "market_lag", "conv_acceleration",
        "k_env", "bullpen_lockdown", "traffic_conversion", "hh_eff", "hh_under",
        "over_pressure_score", "under_suppression_score", "market_value_score", "risk_filter_score",
        "market_discount", "market_discount_score",
        "consensus_total", "market_min_total", "market_max_total", "market_disagreement",
        "line_velocity", "line_direction", "primary_book", "best_book", "best_available_total",
        "best_available_price", "price_adjusted_best_book", "price_adjusted_best_total",
        "price_adjusted_best_price", "market_confirmation_score", "book_count",
        "first_seen_total", "true_opening_total", "recommended_book", "recommended_total",
        "recommended_price", "best_line_age_seconds", "best_line_last_update",
        "book_velocity_summary", "leading_book", "model_probability",
        "break_even_probability", "expected_value", "max_entry_line", "max_entry_price",
        "app_status", "bet_quality", "quality_reason",
        "lineup_pressure_score", "bullpen_context_score", "confidence_decay_score",
        "probability_source", "leading_book_score",
        "scenario", "action", "pattern_tags",
        "market_reaction_profile", "calculated_risk_tier", "suggested_unit", "profile_status",
        "profile_sample", "profile_win_pct", "profile_avg_clv", "profile_confidence_adjustment",
        "actual_entry_line", "actual_entry_price", "actual_entry_book", "actual_result",
        "final_score", "final_total", "result", "graded_at",
    ]


def graded_fieldnames():
    return strike_fieldnames()


def learning_summary_fieldnames():
    return [
        "updated_at", "pattern", "side", "total", "wins", "losses", "pushes",
        "win_pct", "sample_note", "recommendation",
    ]


def build_learning_summary():
    if not ENABLE_SELF_LEARNING:
        return []

    rows = csv_read_rows(GRADED_RESULTS_FILE)
    graded = [r for r in rows if r.get("result") in ["WIN", "LOSS", "PUSH"]]
    if not graded:
        return []

    buckets = {}

    def add_bucket(pattern, row):
        side = str(row.get("side", "")).upper()
        key = (pattern, side)
        node = buckets.setdefault(key, {"total": 0, "wins": 0, "losses": 0, "pushes": 0})
        node["total"] += 1
        if row.get("result") == "WIN":
            node["wins"] += 1
        elif row.get("result") == "LOSS":
            node["losses"] += 1
        elif row.get("result") == "PUSH":
            node["pushes"] += 1

    for row in graded:
        tags = row.get("pattern_tags", "")
        tag_list = [t for t in tags.split("|") if t]

        # Single-pattern buckets.
        for t in tag_list:
            add_bucket(t, row)

        # High-value combinations we care about professionally.
        combos = [
            ("P2R_85_PLUS+CONV_80_PLUS", {"P2R_85_PLUS", "CONV_80_PLUS"}),
            ("STRESS_80_PLUS+P2R_85_PLUS", {"STRESS_80_PLUS", "P2R_85_PLUS"}),
            ("THREAT_70_PLUS+P2R_70_PLUS", {"THREAT_70_PLUS", "P2R_70_PLUS"}),
            ("STACK_5_PLUS+LAG_60_PLUS", {"STACK_5_PLUS", "LAG_60_PLUS"}),
            ("LATE+BASES_EMPTY", {"LATE", "BASES_EMPTY"}),
            ("LATE+PREV_75_PLUS", {"LATE", "PREV_75_PLUS"}),
            ("CONTACT_70_PLUS+CONV_80_PLUS", {"CONTACT_70_PLUS", "CONV_80_PLUS"}),
            ("K_ENV_60_PLUS+BULLPEN_LOCK_55_PLUS", {"K_ENV_60_PLUS", "BULLPEN_LOCK_55_PLUS"}),
            ("TRAFFIC_CONV_55_PLUS+HH_EFF_55_PLUS", {"TRAFFIC_CONV_55_PLUS", "HH_EFF_55_PLUS"}),
            ("K_ENV_60_PLUS+HH_UNDER_55_PLUS", {"K_ENV_60_PLUS", "HH_UNDER_55_PLUS"}),
        ]
        tag_set = set(tag_list)
        for label, required in combos:
            if required.issubset(tag_set):
                add_bucket(label, row)

    output = []
    now = now_local().isoformat()
    for (pattern, side), stats in sorted(buckets.items(), key=lambda kv: (kv[1]["total"], kv[1]["wins"]), reverse=True):
        total = stats["total"]
        if total <= 0:
            continue
        wins = stats["wins"]
        losses = stats["losses"]
        pushes = stats["pushes"]
        denom = max(1, wins + losses)
        win_pct = round((wins / denom) * 100, 1)

        if total < MIN_STRONG_PATTERN_SAMPLE:
            note = "Small sample"
            recommendation = "Track only"
        elif win_pct >= 62:
            note = "Strong historical pattern"
            recommendation = "Boost confidence"
        elif win_pct <= 45:
            note = "Weak historical pattern"
            recommendation = "Caution flag"
        else:
            note = "Neutral historical pattern"
            recommendation = "No adjustment"

        output.append({
            "updated_at": now,
            "pattern": pattern,
            "side": side,
            "total": total,
            "wins": wins,
            "losses": losses,
            "pushes": pushes,
            "win_pct": win_pct,
            "sample_note": note,
            "recommendation": recommendation,
        })

    csv_write_rows(LEARNING_SUMMARY_FILE, learning_summary_fieldnames(), output)
    return output




def american_odds_profit_units(price, result):
    """
    Profit in units assuming 1 unit risked per strike.
    -110 winner = +0.91u, +105 winner = +1.05u, loser = -1u.
    """
    result = str(result or "").upper()
    if result == "PUSH":
        return 0.0
    if result != "WIN":
        return -1.0
    p = safe_int(price, 0)
    if p == 0:
        return 0.0
    if p > 0:
        return round(p / 100.0, 3)
    return round(100.0 / abs(p), 3)


def summarize_record(rows):
    wins = sum(1 for r in rows if r.get("result") == "WIN")
    losses = sum(1 for r in rows if r.get("result") == "LOSS")
    pushes = sum(1 for r in rows if r.get("result") == "PUSH")
    denom = max(1, wins + losses)
    win_pct = round((wins / denom) * 100, 1)
    units = round(sum(american_odds_profit_units(r.get("price"), r.get("result")) for r in rows), 2)
    return wins, losses, pushes, win_pct, units



def build_actionable_recommendations(graded_rows):
    """
    Turns the day's graded results into concrete next-day coaching notes.
    These are directional, not automatic betting changes, until sample size grows.
    """
    if not ENABLE_ACTIONABLE_DAILY_RECOMMENDATIONS:
        return []
    if not graded_rows:
        return []

    notes = []
    overs = [r for r in graded_rows if str(r.get("side", "")).upper() == "OVER"]
    unders = [r for r in graded_rows if str(r.get("side", "")).upper() == "UNDER"]

    def wl(rows):
        return sum(1 for r in rows if r.get("result") == "WIN"), sum(1 for r in rows if r.get("result") == "LOSS")

    late_over_losses = [r for r in overs if r.get("result") == "LOSS" and safe_int(r.get("inning"), 0) >= 7]
    if len(late_over_losses) >= MIN_RECOMMENDATION_SAMPLE:
        notes.append("Reduce late OVER aggression: multiple losses fired in inning 7+.")

    extreme_losses = [r for r in graded_rows if r.get("result") == "LOSS" and safe_float(r.get("line"), 0) >= MAX_EXTREME_TOTAL_STRIKE_LINE]
    if extreme_losses:
        notes.append("Tighten extreme totals: require elite edge/confirmation for live totals 11.5+.")

    under_k_bp = [r for r in unders if safe_int(r.get("k_env"), 0) >= 60 and safe_int(r.get("bullpen_lockdown"), 0) >= 55]
    if len(under_k_bp) >= MIN_RECOMMENDATION_SAMPLE:
        w, l = wl(under_k_bp)
        if w > l:
            notes.append(f"Boost UNDER profile: KEnv 60+ plus BPLock 55+ went {w}-{l} today.")
        elif l > w:
            notes.append(f"Review UNDER profile: KEnv+BPLock underperformed {w}-{l}; inspect screenshots before trusting it.")

    over_traffic = [r for r in overs if safe_int(r.get("traffic_conversion"), 0) >= 55 and safe_int(r.get("hh_eff"), 0) >= 55]
    if len(over_traffic) >= MIN_RECOMMENDATION_SAMPLE:
        w, l = wl(over_traffic)
        if w > l:
            notes.append(f"Boost OVER profile: traffic conversion + hard-hit efficiency went {w}-{l} today.")
        elif l > w:
            notes.append(f"Caution OVER profile: traffic/contact did not convert today ({w}-{l}).")

    high_risk_losses = [r for r in graded_rows if r.get("result") == "LOSS" and safe_int(r.get("risk_filter_score"), 0) >= 55]
    if high_risk_losses:
        notes.append("Risk filter worked as warning: high-risk losses appeared; consider requiring stronger value score.")

    value_winners = [r for r in graded_rows if r.get("result") == "WIN" and safe_int(r.get("market_value_score"), 0) >= 70]
    if len(value_winners) >= MIN_RECOMMENDATION_SAMPLE:
        notes.append("Market value signal helped: prioritize strikes with Value 70+ tomorrow.")

    if not notes:
        notes.append("No strong adjustment from today alone; keep collecting graded strikes and screenshots.")
    return notes[:5]



def independent_decision_rows(rows):
    """
    Collapse same-game same-side duplicates so the learning report separates raw alerts
    from true independent BET NOW decisions.
    """
    seen = set()
    out = []
    for r in rows:
        key = (r.get("date"), r.get("game_pk") or r.get("game"), r.get("side"))
        if r.get("scenario", "").upper().startswith("SHIFT REVERSAL"):
            key = key + ("REVERSAL",)
        if key in seen:
            continue
        seen.add(key)
        out.append(r)
    return out



def profile_dashboard_lines(report_date=None):
    """
    V3.7.5 profile dashboard.
    Treats SHIFT as five separate systems competing for attention/capital:
    Discounted OVER, Continuation OVER, Inflated UNDER, False Inflation Fade,
    and Pitching Dominance UNDER.

    This is reporting only. It does not cap opportunities. Every game can still
    qualify if its own setup clears the decision gates.
    """
    report_date = report_date or today()
    all_rows = [
        r for r in csv_read_rows(GRADED_RESULTS_FILE)
        if r.get("result") in ["WIN", "LOSS", "PUSH"]
    ]
    today_rows = [r for r in all_rows if r.get("date") == report_date]
    profiles = [
        "DISCOUNTED_OVER",
        "CONTINUATION_OVER",
        "INFLATED_UNDER",
        "FALSE_INFLATION_FADE",
        "PITCHING_DOMINANCE_UNDER",
    ]

    lines = []
    lines.append("Profile Dashboard:")
    if ENABLE_UNDER_PROFILE_TEST_ALERTS:
        lines.append("• INFLATED_UNDER and PITCHING_DOMINANCE_UNDER: ACTIVE Tier C / TEST UNIT alert mode.")
    if ENABLE_NEUTRAL_MARKET_WATCH_ONLY:
        lines.append("• NEUTRAL_MARKET: WATCH ONLY / research bucket — no BET NOW SMS promotion.")
    if not all_rows:
        lines.append("• Building sample — no profile results stored yet.")
        return lines

    for profile in profiles:
        rows = [r for r in all_rows if (r.get("market_reaction_profile") or r.get("profile") or "UNCLASSIFIED") == profile]
        today_profile = [r for r in today_rows if (r.get("market_reaction_profile") or r.get("profile") or "UNCLASSIFIED") == profile]
        if rows:
            w, l, p, pct, units = summarize_record(rows)
            tw, tl, tp, tpct, tunits = summarize_record(today_profile) if today_profile else (0, 0, 0, 0, 0.0)
            clvs = [safe_float(r.get("clv"), 0) for r in rows if str(r.get("clv", "")).strip() != ""]
            avg_clv = round(avg(clvs), 2) if clvs else 0.0
            tier_counts = {}
            for r in rows:
                tier_counts[r.get("calculated_risk_tier") or "UNTRACKED"] = tier_counts.get(r.get("calculated_risk_tier") or "UNTRACKED", 0) + 1
            tier_text = ", ".join(f"{k}:{v}" for k, v in sorted(tier_counts.items())) if tier_counts else "no tiers"
            status = profile_stats_from_rows(profile).get("profile_status", "OPEN_TEST")
            lines.append(
                f"• {profile}: ALL {w}-{l}-{p} | {pct}% | {units:+.2f}u | CLV {avg_clv:+.2f} | {status} | Tiers {tier_text}"
            )
            if today_profile:
                lines.append(f"  Today: {tw}-{tl}-{tp} | {tpct}% | {tunits:+.2f}u")
        else:
            lines.append(f"• {profile}: no graded sample yet | OPEN_TEST")

    near_misses = [r for r in csv_read_rows(PROFILE_NEAR_MISS_FILE) if r.get("date") == report_date]
    if near_misses:
        by_profile = {}
        for r in near_misses:
            by_profile.setdefault(r.get("profile") or "UNCLASSIFIED", 0)
            by_profile[r.get("profile") or "UNCLASSIFIED"] += 1
        nm_text = ", ".join(f"{k}:{v}" for k, v in sorted(by_profile.items()))
        lines.append(f"Near-Misses Today: {nm_text}")
    else:
        lines.append("Near-Misses Today: none logged")

    for research_line in profile_research_summary_lines(report_date):
        lines.append(research_line)

    lines.append("Dashboard rule: no daily caps; profile history informs confidence/tiering only.")
    return lines

def generate_daily_learning_report(report_date=None):
    """
    Creates a professional daily report from graded_results.csv, learning_summary.csv,
    and clv_history.csv. This surfaces what the model learned instead of forcing
    the user to interpret CSV files manually.
    """
    report_date = report_date or today()
    graded = [
        r for r in csv_read_rows(GRADED_RESULTS_FILE)
        if r.get("date") == report_date and r.get("result") in ["WIN", "LOSS", "PUSH"]
    ]

    all_graded = [
        r for r in csv_read_rows(GRADED_RESULTS_FILE)
        if r.get("result") in ["WIN", "LOSS", "PUSH"]
    ]

    build_learning_summary()
    summary_rows = csv_read_rows(LEARNING_SUMMARY_FILE)

    lines = []
    lines.append(f"📊 SHIFT MLB DAILY LEARNING REPORT")
    lines.append(f"Date: {report_date}")
    lines.append("")

    if not graded:
        lines.append("No graded STRIKE results found yet for today.")
        lines.append("Check Railway logs for SELF-LEARNING STORED STRIKE and SELF-LEARNING GRADED.")
        return "\n".join(lines)

    w, l, p, pct, units = summarize_record(graded)
    lines.append(f"Raw Alerts Today: {w}-{l}-{p} | {pct}% | {units:+.2f}u")
    independent = independent_decision_rows(graded)
    iw, il, ip, ipct, iunits = summarize_record(independent)
    lines.append(f"Independent Decisions: {iw}-{il}-{ip} | {ipct}% | {iunits:+.2f}u")

    over_rows = [r for r in graded if str(r.get("side", "")).upper() == "OVER"]
    under_rows = [r for r in graded if str(r.get("side", "")).upper() == "UNDER"]
    if over_rows:
        ow, ol, op, opct, ounits = summarize_record(over_rows)
        lines.append(f"OVER: {ow}-{ol}-{op} | {opct}% | {ounits:+.2f}u")
    if under_rows:
        uw, ul, up, upct, uunits = summarize_record(under_rows)
        lines.append(f"UNDER: {uw}-{ul}-{up} | {upct}% | {uunits:+.2f}u")

    profile_map = {}
    for r in graded:
        profile = r.get("market_reaction_profile") or r.get("profile") or "UNCLASSIFIED"
        profile_map.setdefault(profile, []).append(r)
    if profile_map:
        lines.append("")
        lines.append("Market-Reaction Profile Buckets:")
        for profile, rows in sorted(profile_map.items(), key=lambda kv: len(kv[1]), reverse=True):
            lines.append("• " + summarize_bucket(profile, rows))

    tier_map = {}
    for r in graded:
        tier = r.get("calculated_risk_tier") or "UNTRACKED"
        tier_map.setdefault(tier, []).append(r)
    if tier_map:
        lines.append("")
        lines.append("Calculated-Risk Tiers:")
        for tier, rows in sorted(tier_map.items(), key=lambda kv: str(kv[0])):
            lines.append("• " + summarize_bucket(f"Tier {tier}", rows))

    if all_graded:
        aw, al, ap, apct, aunits = summarize_record(all_graded)
        lines.append(f"All-Time Stored: {aw}-{al}-{ap} | {apct}% | {aunits:+.2f}u")

    lines.append("")
    for dash_line in profile_dashboard_lines(report_date):
        lines.append(dash_line)

    clv_rows = [r for r in csv_read_rows(CLV_HISTORY_FILE) if r.get("date") == report_date]
    if clv_rows:
        beat = sum(1 for r in clv_rows if str(r.get("beat_market", "")).lower() == "true")
        total = len(clv_rows)
        avg_clv = round(avg([safe_float(r.get("clv"), 0) for r in clv_rows]), 2)
        lines.append(f"CLV: beat market {beat}/{total} | Avg CLV {avg_clv:+.2f}")

    lines.append("")
    lines.append("Market-Quality Buckets:")
    bucket_map = {}
    for r in graded:
        for tag in market_quality_tags(r):
            bucket_map.setdefault(tag, []).append(r)
    if bucket_map:
        for tag, rows in sorted(bucket_map.items(), key=lambda kv: len(kv[1]), reverse=True)[:8]:
            lines.append("• " + summarize_bucket(tag, rows))
    else:
        lines.append("No market-quality buckets yet.")

    quality_map = {}
    for r in graded:
        quality_map.setdefault(r.get("bet_quality") or classify_bet_quality(r)[0], []).append(r)
    if quality_map:
        lines.append("")
        lines.append("Bet-Quality Buckets:")
        for tag, rows in sorted(quality_map.items(), key=lambda kv: len(kv[1]), reverse=True):
            lines.append("• " + summarize_bucket(tag, rows))

    lines.append("")
    for dl in decision_report_lines(report_date):
        lines.append(dl)

    lines.append("")
    for al in adaptive_report_lines():
        lines.append(al)

    lines.append("")
    lines.append("Sample Discipline:")
    lines.append(f"• Strong-pattern label requires {MIN_STRONG_PATTERN_SAMPLE}+ graded decisions.")
    lines.append(f"• Automatic threshold changes should wait for {MIN_AUTO_ADJUST_SAMPLE}+ independent decisions.")
    lines.append("• No daily profile caps: every game can qualify if its own setup clears the gates.")
    lines.append("• A/B/C tiers guide exposure size, not whether the bot stops looking for opportunity.")
    lines.append("• Profile Dashboard treats the five scenarios as separate systems competing for capital.")

    lines.append("")
    lines.append("Best Current Patterns:")
    qualified = [r for r in summary_rows if safe_int(r.get("total"), 0) >= MIN_PATTERN_SAMPLE_FOR_REPORT]
    strong = sorted(
        [r for r in qualified if safe_float(r.get("win_pct"), 0) >= 60],
        key=lambda r: (safe_float(r.get("win_pct"), 0), safe_int(r.get("total"), 0)),
        reverse=True,
    )[:5]
    if strong:
        for r in strong:
            lines.append(f"✅ {r.get('side')} {r.get('pattern')}: {r.get('wins')}-{r.get('losses')}-{r.get('pushes')} ({r.get('win_pct')}%)")
    else:
        lines.append("Building sample — no strong pattern with enough volume yet.")

    lines.append("")
    lines.append("Caution Patterns:")
    weak = sorted(
        [r for r in qualified if safe_float(r.get("win_pct"), 100) <= 45],
        key=lambda r: (safe_float(r.get("win_pct"), 100), -safe_int(r.get("total"), 0)),
    )[:5]
    if weak:
        for r in weak:
            lines.append(f"⚠️ {r.get('side')} {r.get('pattern')}: {r.get('wins')}-{r.get('losses')}-{r.get('pushes')} ({r.get('win_pct')}%)")
    else:
        lines.append("No clear caution pattern yet.")

    lines.append("")
    lines.append("Actionable Next-Day Notes:")
    for note in build_actionable_recommendations(graded):
        lines.append(f"• {note}")
    lines.append("")
    lines.append("Profile Learning Gates:")
    profile_learning_rows = []
    for profile in ["DISCOUNTED_OVER", "CONTINUATION_OVER", "INFLATED_UNDER", "FALSE_INFLATION_FADE", "PITCHING_DOMINANCE_UNDER"]:
        stats = profile_stats_from_rows(profile)
        profile_learning_rows.append(stats)
        lines.append(f"• {profile}: {stats['wins']}-{stats['losses']}-{stats['pushes']} | {stats['win_pct']}% | CLV {stats['avg_clv']:+.2f} | {stats['profile_status']}")
    csv_write_rows(PROFILE_LEARNING_SUMMARY_FILE, profile_learning_fieldnames(), profile_learning_rows)

    lines.append("")
    lines.append("Use one-day notes as coaching, not proof. Make threshold changes only after repeat patterns show enough independent decisions.")
    post_tracking_event("daily_learning_report", {"date": report_date, "text": "\n".join(lines)})
    return "\n".join(lines)


def send_admin_text(msg):
    """
    Sends non-STRIKE administrative summaries such as the daily learning report.
    This bypasses STRIKE-only SMS filtering intentionally.
    """
    print("\n" + msg + "\n")
    if not SEND_DAILY_LEARNING_REPORT_SMS:
        print("DAILY REPORT SMS NOT SENT: SMS disabled by SEND_DAILY_LEARNING_REPORT_SMS.")
        return
    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_TO_NUMBER]):
        print("DAILY REPORT SMS NOT SENT: Missing Twilio variables.")
        return
    try:
        body = msg
        if len(body) > MAX_SHORT_SMS_CHARS:
            body = body[:MAX_SHORT_SMS_CHARS - 30].rstrip() + "\n[Trimmed]"
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(body=body, from_=TWILIO_FROM_NUMBER, to=ALERT_TO_NUMBER)
        print("DAILY REPORT TEXT SENT SUCCESSFULLY")
    except Exception as e:
        print("DAILY REPORT TEXT ERROR:", repr(e))



def daily_rows_for_email(path, report_date=None, graded_only=False):
    report_date = report_date or today()
    rows = [r for r in csv_read_rows(path) if r.get("date") == report_date]
    if graded_only:
        rows = [r for r in rows if r.get("result") in ["WIN", "LOSS", "PUSH"]]
    return rows


def compact_row_for_email(row, include_result=True):
    result = row.get("result", "") if include_result else row.get("action", "")
    game = row.get("game", "")
    side = row.get("side", "")
    line = row.get("line", "")
    price = row.get("price", "")
    book = row.get("recommended_book") or row.get("price_adjusted_best_book") or row.get("best_book") or row.get("primary_book") or ""
    final = row.get("final_score", "")
    extras = [
        f"EV {row.get('expected_value', '')}",
        f"MktConf {row.get('market_confirmation_score', '')}",
        f"Disc {row.get('market_discount', '')}",
        f"Vel {row.get('line_velocity', '')}",
        f"Lineup {row.get('lineup_pressure_score', '')}",
        f"Bullpen {row.get('bullpen_context_score', '')}",
        f"Quality {row.get('bet_quality', '')}",
        f"Profile {row.get('market_reaction_profile', '')}",
        f"Tier {row.get('calculated_risk_tier', '')}",
    ]
    prefix = f"{result}: " if result else ""
    final_text = f" | Final {final}" if final else ""
    return f"• {prefix}{game} | {side} {line} ({price}) at {book}{final_text} | " + " | ".join(extras)


def generate_nightly_email_body(report_date=None):
    report_date = report_date or today()
    report = generate_daily_learning_report(report_date)
    strikes = daily_rows_for_email(STRIKE_HISTORY_FILE, report_date, graded_only=False)
    results = daily_rows_for_email(GRADED_RESULTS_FILE, report_date, graded_only=True)

    lines = []
    lines.append(report)
    lines.append("\n" + "=" * 60)
    lines.append("ALL SHIFT STRIKES")
    lines.append("=" * 60)
    if strikes:
        for r in strikes:
            if r.get("action") == "STRIKE":
                lines.append(compact_row_for_email(r, include_result=False))
    else:
        lines.append("No STRIKE rows stored for today.")

    lines.append("\n" + "=" * 60)
    lines.append("ALL SHIFT RESULTS")
    lines.append("=" * 60)
    if results:
        for r in results:
            lines.append(compact_row_for_email(r, include_result=True))
    else:
        lines.append("No graded results stored for today yet.")

    lines.append("\nFiles attached when available: strike_history.csv, graded_results.csv, learning_summary.csv, clv_history.csv, profile_near_misses.csv, actual_entries.csv. Actual-entry fields are included for manual correction when you beat the bot number.")
    return "\n".join(lines)


def attach_csv_if_exists(message, path):
    if not path or not os.path.exists(path):
        return
    try:
        with open(path, "rb") as f:
            data = f.read()
        message.add_attachment(data, maintype="text", subtype="csv", filename=os.path.basename(path))
    except Exception as e:
        print(f"EMAIL ATTACHMENT ERROR {path}:", repr(e))


def send_nightly_summary_email(report_date=None):
    if not ENABLE_NIGHTLY_EMAIL_REPORT:
        print("NIGHTLY EMAIL NOT SENT: disabled by ENABLE_NIGHTLY_EMAIL_REPORT.")
        return False
    if not NIGHTLY_EMAIL_TO:
        print("NIGHTLY EMAIL NOT SENT: NIGHTLY_EMAIL_TO missing.")
        return False
    if not all([SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD]):
        print("NIGHTLY EMAIL NOT SENT: SMTP settings missing. Add SMTP_USER and SMTP_PASSWORD in Railway variables.")
        return False

    report_date = report_date or today()
    body = generate_nightly_email_body(report_date)
    subject = f"{NIGHTLY_EMAIL_SUBJECT_PREFIX} — {report_date}"

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = EMAIL_FROM or SMTP_USER
    msg["To"] = NIGHTLY_EMAIL_TO
    msg.set_content(body)

    if ATTACH_DAILY_CSVS_TO_EMAIL:
        for path in [STRIKE_HISTORY_FILE, GRADED_RESULTS_FILE, LEARNING_SUMMARY_FILE, CLV_HISTORY_FILE]:
            attach_csv_if_exists(msg, path)

    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=20) as server:
            if SMTP_USE_TLS:
                server.starttls()
            server.login(SMTP_USER, SMTP_PASSWORD)
            server.send_message(msg)
        print(f"NIGHTLY SUMMARY EMAIL SENT to {NIGHTLY_EMAIL_TO}")
        return True
    except Exception as e:
        print("NIGHTLY SUMMARY EMAIL ERROR:", repr(e))
        return False


def strongest_signal_pairs(row, limit=5):
    """
    Converts the model scores saved with the STRIKE into short, readable signal labels.
    This is what the result text uses to explain why a strike won or lost.
    """
    candidates = [
        ("Proj", safe_int(row.get("projection_score"), 0)),
        ("Confirm", safe_int(row.get("confirmation_score"), 0)),
        ("P2R", safe_int(row.get("p2r"), 0)),
        ("Conv", safe_int(row.get("conv"), 0)),
        ("Prev", safe_int(row.get("prev"), 0)),
        ("Stress", safe_int(row.get("stress"), 0)),
        ("Contact", safe_int(row.get("contact"), 0)),
        ("KEnv", safe_int(row.get("k_env"), 0)),
        ("BPLock", safe_int(row.get("bullpen_lockdown"), 0)),
        ("TConv", safe_int(row.get("traffic_conversion"), 0)),
        ("HHEff", safe_int(row.get("hh_eff"), 0)),
        ("HHUnder", safe_int(row.get("hh_under"), 0)),
        ("OverMaster", safe_int(row.get("over_pressure_score"), 0)),
        ("UnderMaster", safe_int(row.get("under_suppression_score"), 0)),
        ("Value", safe_int(row.get("market_value_score"), 0)),
        ("Risk", safe_int(row.get("risk_filter_score"), 0)),
        ("Threat", safe_int(row.get("threat_index"), 0)),
        ("Stack", safe_int(row.get("signal_stack"), 0)),
        ("Lag", safe_int(row.get("market_lag"), 0)),
    ]
    strong = [(name, val) for name, val in candidates if val > 0]
    strong.sort(key=lambda x: x[1], reverse=True)
    return strong[:limit]


def build_result_learning_note(row):
    """
    Gives one plain-English improvement note after a strike is graded.
    This is intentionally simple: it helps us refine day-to-day without pretending
    one result is a statistically proven pattern.
    """
    side = str(row.get("side", "")).upper()
    result = str(row.get("result", "")).upper()
    line = safe_float(row.get("line"), 0)
    inning = safe_int(row.get("inning"), 0)
    k_env = safe_int(row.get("k_env"), 0)
    bp = safe_int(row.get("bullpen_lockdown"), 0)
    tconv = safe_int(row.get("traffic_conversion"), 0)
    hheff = safe_int(row.get("hh_eff"), 0)
    hhunder = safe_int(row.get("hh_under"), 0)
    conv = safe_int(row.get("conv"), 0)
    prev = safe_int(row.get("prev"), 0)
    p2r = safe_int(row.get("p2r"), 0)

    if result == "WIN":
        if side == "UNDER" and (k_env >= 60 or bp >= 55 or hhunder >= 55 or prev >= 60):
            return "Keep: UNDER profile supported by strikeouts/bullpen/run prevention."
        if side == "OVER" and (tconv >= 55 or hheff >= 55 or conv >= 65 or p2r >= 70):
            return "Keep: OVER profile supported by traffic/contact/run-conversion pressure."
        return "Keep tracking: winner, but signal profile needs more sample."

    if result == "LOSS":
        if side == "OVER" and line >= MAX_EXTREME_TOTAL_STRIKE_LINE:
            return "Caution: high live OVER total lost; require elite confirmation/edge next time."
        if side == "UNDER" and line >= MAX_EXTREME_TOTAL_STRIKE_LINE:
            return "Caution: high live UNDER total lost; check if market spike was justified."
        if side == "OVER" and inning >= 7:
            return "Caution: late OVER lost; reduce confidence unless traffic/contact is elite."
        if side == "OVER" and tconv < 55 and hheff < 55:
            return "Caution: OVER lost without strong traffic conversion or hard-hit efficiency."
        if side == "UNDER" and k_env < 60 and bp < 55:
            return "Caution: UNDER lost without strong K environment or bullpen lockdown."
        return "Review: loser added to pattern database; wait for repeat signal before changing logic."

    if result == "PUSH":
        return "Neutral: push stored; useful for line-quality review."

    return "Stored: result added to learning database."


def build_graded_result_message(row, todays_rows):
    """
    Builds the immediate postgame result text. This is the missing user-facing loop:
    STRIKE -> final score -> result -> today record -> one improvement note.
    """
    result = str(row.get("result", "UNKNOWN")).upper()
    emoji = "✅" if result == "WIN" else "❌" if result == "LOSS" else "➖" if result == "PUSH" else "📌"
    w, l, p, pct, units = summarize_record(todays_rows)

    signals = strongest_signal_pairs(row, limit=5)
    signal_text = " | ".join([f"{name} {val}" for name, val in signals]) if signals else "Signals unavailable"
    tags = row.get("pattern_tags", "") or "No tags"
    if len(tags) > 120:
        tags = tags[:117] + "..."

    msg = [
        f"{emoji} SHIFT RESULT",
        f"{row.get('game', 'Unknown Game')}",
        f"Play: {row.get('side')} {row.get('line')} ({row.get('price') or 'price n/a'})",
        f"Final: {row.get('final_score')} | Total {row.get('final_total')} | {result}",
        f"Alert: {row.get('score')} | {row.get('inning_state')} {row.get('inning')} | {row.get('base_out')}",
        f"Open/Live/Proj: {row.get('opening_total')}/{row.get('live_total')}/{row.get('projected_total')} | Edge {row.get('edge')}",
        f"Signals: {signal_text}",
        f"Master: OVER {row.get('over_pressure_score') or 0} | UNDER {row.get('under_suppression_score') or 0} | Value {row.get('market_value_score') or 0} | Risk {row.get('risk_filter_score') or 0}",
        f"Today: {w}-{l}-{p} | {pct}% | {units:+.2f}u",
        build_result_learning_note(row),
    ]
    return "\n".join(msg)


def send_graded_result_text(msg):
    """
    Sends immediate graded result SMS. Keeps logging even if SMS is disabled/misconfigured.
    """
    if ENABLE_GRADED_RESULT_LOG:
        print("\n" + msg + "\n")

    if not ENABLE_GRADED_RESULT_SMS:
        print("GRADED RESULT SMS NOT SENT: disabled by ENABLE_GRADED_RESULT_SMS.")
        return

    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_TO_NUMBER]):
        print("GRADED RESULT SMS NOT SENT: Missing Twilio variables.")
        return

    body = msg
    if len(body) > MAX_RESULT_SMS_CHARS:
        body = body[:MAX_RESULT_SMS_CHARS - 30].rstrip() + "\n[Trimmed]"

    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(body=body, from_=TWILIO_FROM_NUMBER, to=ALERT_TO_NUMBER)
        print("GRADED RESULT TEXT SENT SUCCESSFULLY")
    except Exception as e:
        print("GRADED RESULT TEXT ERROR:", repr(e))


def maybe_send_daily_learning_report(state, any_live):
    """
    Sends one end-of-night report per day after the configured hour once no games are live.

    V3.6.1 fix:
    - Nightly email is no longer hidden behind ENABLE_DAILY_LEARNING_REPORT.
    - Logs a clear NIGHTLY EMAIL CHECK line so Railway confirms the code path.
    - Uses separate state keys for SMS/log report and email report.
    """
    if any_live:
        return
    current_hour = now_local().hour
    if current_hour < DAILY_LEARNING_REPORT_HOUR:
        return

    report_date = today()
    report = None
    changed_state = False

    print(
        f"NIGHTLY EMAIL CHECK | date={report_date} | hour={current_hour} | "
        f"enabled={ENABLE_NIGHTLY_EMAIL_REPORT} | to={NIGHTLY_EMAIL_TO or 'MISSING'}"
    )

    daily_sent_key = "daily_learning_report_sent_for"
    if ENABLE_DAILY_LEARNING_REPORT and state.get(daily_sent_key) != report_date:
        report = report or generate_daily_learning_report(report_date)
        print(f"DAILY LEARNING REPORT GENERATED | {report_date}")
        if SEND_DAILY_LEARNING_REPORT_SMS:
            send_admin_text(report)
        else:
            print("DAILY LEARNING REPORT SMS SKIPPED: SEND_DAILY_LEARNING_REPORT_SMS=false")
        state[daily_sent_key] = report_date
        changed_state = True

    email_sent_key = "nightly_email_report_sent_for"
    if ENABLE_NIGHTLY_EMAIL_REPORT and state.get(email_sent_key) != report_date:
        email_sent = send_nightly_summary_email(report_date)
        if email_sent:
            state[email_sent_key] = report_date
            changed_state = True
        else:
            print(f"NIGHTLY EMAIL NOT MARKED SENT | {report_date}")
    elif not ENABLE_NIGHTLY_EMAIL_REPORT:
        print("NIGHTLY EMAIL SKIPPED: ENABLE_NIGHTLY_EMAIL_REPORT=false")
    else:
        print(f"NIGHTLY EMAIL SKIPPED: already sent for {report_date}")

    if changed_state:
        save_state(state)

def historical_pattern_note(info, opportunity):
    """
    Reads our own graded history and returns a short note for the alert.
    This does not block alerts. It only informs us whether similar past alerts
    have been strong, weak, or still too small of a sample.
    """
    if not ENABLE_SELF_LEARNING:
        return "Historical Match: tracking disabled"

    scores = opportunity.get("scores", {})
    row = {
        "side": opportunity.get("side"),
        "inning": info.get("inning"),
        "outs": info.get("outs"),
        "base_out": f"{info.get('base_state', {}).get('label', '')}, {info.get('outs')} out(s)",
        "stress": scores.get("pitcher_stress"),
        "contact": scores.get("contact_quality"),
        "p2r": scores.get("pressure_to_runs"),
        "conv": scores.get("run_conversion"),
        "prev": scores.get("run_prevention"),
        "pred_move": scores.get("predictive_market_move"),
        "threat_index": scores.get("threat_index"),
        "signal_stack": scores.get("signal_stack"),
        "market_lag": scores.get("market_lag"),
        "k_env": scores.get("strikeout_environment"),
        "bullpen_lockdown": scores.get("bullpen_lockdown"),
        "traffic_conversion": scores.get("traffic_conversion"),
        "hh_eff": scores.get("hard_hit_efficiency"),
        "hh_under": scores.get("hard_hit_under_support"),
    }

    tags = set(pattern_tags_from_row(row))
    graded = [r for r in csv_read_rows(GRADED_RESULTS_FILE) if r.get("result") in ["WIN", "LOSS", "PUSH"]]

    if not graded:
        return "Historical Match: building sample"

    best = None
    for r in graded:
        r_tags = set((r.get("pattern_tags") or "").split("|"))
        overlap = len(tags.intersection(r_tags))
        if str(r.get("side", "")).upper() == str(opportunity.get("side", "")).upper():
            overlap += 2
        if overlap <= 1:
            continue
        if best is None or overlap > best[0]:
            best = (overlap, [])

    # Instead of nearest-neighbor only, use all rows with strong tag overlap.
    similar = []
    for r in graded:
        r_tags = set((r.get("pattern_tags") or "").split("|"))
        overlap = len(tags.intersection(r_tags))
        if str(r.get("side", "")).upper() == str(opportunity.get("side", "")).upper():
            overlap += 2
        if overlap >= 4:
            similar.append(r)

    if len(similar) < 5:
        return f"Historical Match: small sample ({len(similar)} similar)"

    wins = sum(1 for r in similar if r.get("result") == "WIN")
    losses = sum(1 for r in similar if r.get("result") == "LOSS")
    pushes = sum(1 for r in similar if r.get("result") == "PUSH")
    denom = max(1, wins + losses)
    pct = round((wins / denom) * 100, 1)

    if pct >= 62:
        strength = "Strong"
    elif pct <= 45:
        strength = "Caution"
    else:
        strength = "Neutral"

    return f"Historical Match: {wins}-{losses}-{pushes} ({pct}%) | {strength}"


def grade_completed_strikes(game_pk, label, final_score):
    """
    When a game goes final, grade all stored STRIKE rows for that game.
    This is the core self-learning loop:
    strike alert -> final score -> WIN/LOSS/PUSH -> pattern database.
    """
    if not ENABLE_SELF_LEARNING:
        return

    final_total = final_total_from_score(final_score)
    if final_total is None:
        return

    # V3.7.9: grade research candidates even when no SMS strike was sent.
    grade_profile_research_candidates(game_pk, label, final_score)

    # V3.9.0: grade the master decision database too. This includes BET_NOW,
    # TEST_UNIT, RESEARCH_ONLY, and NO_BET audits.
    grade_completed_decision_log(game_pk, label, final_score)

    rows = csv_read_rows(STRIKE_HISTORY_FILE)
    if not rows:
        return

    changed = False
    newly_graded = []

    for row in rows:
        same_game = (
            row.get("date") == today()
            and (
                row.get("game_pk") == str(game_pk)
                or row.get("game") == label
                or row.get("game_key") == f"{today()}::{label}"
            )
        )
        if not same_game:
            continue
        if row.get("action") != "STRIKE":
            continue
        if row.get("result") in ["WIN", "LOSS", "PUSH"]:
            continue

        result = grade_bet(row.get("side"), row.get("line"), final_total)
        row["final_score"] = final_score
        row["final_total"] = final_total
        row["result"] = result
        row["graded_at"] = now_local().isoformat()
        if not row.get("pattern_tags"):
            row["pattern_tags"] = "|".join(pattern_tags_from_row(row))
        newly_graded.append(dict(row))
        changed = True

    if changed:
        csv_write_rows(STRIKE_HISTORY_FILE, strike_fieldnames(), rows)

        existing = csv_read_rows(GRADED_RESULTS_FILE)
        existing_ids = {r.get("strike_id") for r in existing}
        for row in newly_graded:
            if row.get("strike_id") not in existing_ids:
                existing.append(row)
                existing_ids.add(row.get("strike_id"))
        csv_write_rows(GRADED_RESULTS_FILE, graded_fieldnames(), existing)

        summary = build_learning_summary()
        wins = sum(1 for r in newly_graded if r.get("result") == "WIN")
        losses = sum(1 for r in newly_graded if r.get("result") == "LOSS")
        pushes = sum(1 for r in newly_graded if r.get("result") == "PUSH")
        print(
            f"SELF-LEARNING GRADED | {label} | Final {final_score} | "
            f"New grades W-L-P: {wins}-{losses}-{pushes} | "
            f"Summary patterns: {len(summary)}"
        )

        # V2.4.1: immediately tell the user how each completed STRIKE played out.
        todays_rows = [
            r for r in existing
            if r.get("date") == today() and r.get("result") in ["WIN", "LOSS", "PUSH"]
        ]
        for row in newly_graded:
            post_tracking_event("strike_graded", row)
            post_tracking_event("graded_result", row)
            send_graded_result_text(build_graded_result_message(row, todays_rows))

def time_remaining_multiplier(info):
    """
    Moderate time-remaining adjustment.
    It reduces late-game projection aggression without handcuffing early/mid-game winners.
    """
    if not ENABLE_TIME_REMAINING_ADJUSTMENT:
        return 1.0

    inning = safe_int(info.get("inning", 1), 1)

    if inning <= 4:
        return TIME_ADJ_INNING_1_4
    if inning <= 6:
        return TIME_ADJ_INNING_5_6
    if inning <= 8:
        return TIME_ADJ_INNING_7_8
    return TIME_ADJ_INNING_9_PLUS


def adjusted_projection_for_time(info, live_total, projected_total):
    """
    Adjusts only the EDGE between live and projected line, not total runs directly.
    This preserves early signals while avoiding wild late projections.
    """
    if live_total is None or projected_total is None:
        return projected_total

    mult = time_remaining_multiplier(info)
    edge = projected_total - live_total
    adjusted = live_total + (edge * mult)
    return round(adjusted, 1)


def strike_key(info, side):
    return f"{today()}::{info.get('away','')}@{info.get('home','')}::{side}"


def should_allow_strike(state, info, opportunity):
    """
    Legacy compatibility wrapper.
    V2.6 uses v26_final_betnow_gate() + should_alert() as the only live SMS gate.
    Keeping this no-op prevents old call paths from reintroducing side-based duplicate logic.
    """
    return True, "legacy duplicate gate bypassed by V2.6 single decision engine"


def record_strike_lock(state, info, opportunity):
    """
    Legacy compatibility wrapper.
    Active thesis is recorded by record_active_thesis() after an approved BET NOW.
    This intentionally does nothing so we do not maintain two competing lock systems.
    """
    return


def game_thesis_key(info):
    """
    One game gets one active thesis: OVER, UNDER, or NO_PLAY.
    This is different from the old side-based alert key and prevents the bot
    from firing both sides of the same game as normal STRIKES.
    """
    return f"{today()}::{info.get('game_pk') or info.get('away','') + '@' + info.get('home','')}::THESIS"


def inning_float(info):
    inning = safe_float(info.get("inning"), 0)
    state = str(info.get("inning_state") or "").lower()
    if state.startswith("middle") or state.startswith("end"):
        return inning + 0.5
    return inning


def is_bases_empty(info):
    label = str((info.get("base_state") or {}).get("label", "")).lower()
    return "bases empty" in label or label.strip() in ["empty", "none", ""]


def is_inning_transition(info):
    state = str(info.get("inning_state") or "").lower()
    outs = safe_int(info.get("outs"), 0)
    return outs >= 3 or state.startswith("middle") or state.startswith("end")


def thesis_summary_text(thesis):
    if not thesis:
        return "none"
    return (
        f"{thesis.get('side')} {thesis.get('line')} | "
        f"inning {thesis.get('inning')} | edge {thesis.get('edge')} | "
        f"conf {thesis.get('confidence')}"
    )


def professional_block_reason(info, opportunity):
    """
    V2.5 hard filters for STRIKE accuracy.
    Returns None if the STRIKE remains eligible.
    Returns a readable reason when the STRIKE should be blocked.
    """
    if not ENABLE_PRO_ACCURACY_MODE:
        return None
    if not opportunity or opportunity.get("action") != "STRIKE":
        return None

    side = str(opportunity.get("side", "")).upper()
    scores = opportunity.get("scores", {}) or {}
    line = safe_float(opportunity.get("line"), 0)
    edge_abs = abs(safe_float(opportunity.get("edge"), 0))
    inning = safe_int(info.get("inning"), 0)
    outs = safe_int(info.get("outs"), 0)

    conf = safe_int(scores.get("confirmation_score"), safe_int(opportunity.get("confidence"), 0))
    proj = safe_int(scores.get("projection_score"), 0)
    p2r = safe_int(scores.get("pressure_to_runs"), 0)
    conv = safe_int(scores.get("run_conversion"), 0)
    contact = safe_int(scores.get("contact_quality"), 0)
    tconv = safe_int(scores.get("traffic_conversion"), 0)
    k_env = safe_int(scores.get("strikeout_environment"), 0)
    bp = safe_int(scores.get("bullpen_lockdown"), 0)

    if side == "OVER":
        if BLOCK_OVER_ON_THREE_OUTS and outs >= 3:
            return "blocked: OVER at 3 outs / inning already closed"
        if BLOCK_OVER_AT_INNING_TRANSITION and is_bases_empty(info) and is_inning_transition(info):
            return "blocked: OVER at inning transition with bases empty"
        if BLOCK_CONTACT_ONLY_OVER and contact >= 70 and p2r < MIN_CONTACT_ONLY_P2R and tconv < MIN_CONTACT_ONLY_TRAFFIC_CONV:
            return "blocked: contact-only OVER without enough pressure-to-runs/traffic conversion"
        if line >= MAX_EXTREME_TOTAL_STRIKE_LINE:
            elite = (
                edge_abs >= MIN_EXTREME_TOTAL_EDGE_V25
                and conf >= MIN_EXTREME_TOTAL_CONFIRMATION_V25
                and proj >= MIN_EXTREME_TOTAL_PROJECTION_V25
                and p2r >= MIN_EXTREME_TOTAL_P2R_V25
                and conv >= MIN_EXTREME_TOTAL_CONV_V25
            )
            if not elite:
                return "blocked: extreme live OVER total without elite confirmation"

    if side == "UNDER":
        if inning < MIN_EARLY_UNDER_INNING:
            elite_early_under = (
                edge_abs >= MIN_EARLY_UNDER_EDGE
                and k_env >= MIN_EARLY_UNDER_K_ENV
                and bp >= MIN_EARLY_UNDER_BULPEN_LOCK
                and contact <= MAX_EARLY_UNDER_CONTACT
                and p2r <= MAX_EARLY_UNDER_P2R
            )
            if not elite_early_under:
                return "blocked: early UNDER without elite K/bullpen/dead-contact profile"
        if k_env < 50 and bp < 45 and safe_int(scores.get("run_prevention"), 0) < 70:
            return "blocked: UNDER lacks K environment, bullpen lockdown, and run-prevention support"

    return None


def reversal_allowed(state_game, info, opportunity):
    """
    Opposite-side STRIKES are blocked unless this is a true thesis reversal.
    The reversal text is deliberately labeled so the user knows the recommendation changed.
    """
    if not ENABLE_SHIFT_REVERSAL:
        return False, "reversal disabled"

    thesis = state_game.get("active_thesis")
    if not thesis:
        return False, "no prior thesis"
    old_side = str(thesis.get("side", "")).upper()
    new_side = str(opportunity.get("side", "")).upper()
    if not old_side or old_side == new_side:
        return False, "not opposite side"

    scores = opportunity.get("scores", {}) or {}
    innings_passed = inning_float(info) - safe_float(thesis.get("inning_float"), safe_float(thesis.get("inning"), 0))
    conf = safe_int(opportunity.get("confidence"), 0)
    edge_abs = abs(safe_float(opportunity.get("edge"), 0))
    proj_score = safe_int(scores.get("projection_score"), 0)
    confirm_score = safe_int(scores.get("confirmation_score"), 0)

    ok = (
        innings_passed >= MIN_REVERSAL_INNINGS_PASSED
        and conf >= MIN_REVERSAL_CONFIDENCE
        and edge_abs >= MIN_REVERSAL_EDGE
        and proj_score >= MIN_REVERSAL_PROJECTION_SCORE
        and confirm_score >= MIN_REVERSAL_CONFIRMATION_SCORE
    )
    if ok:
        return True, "true reversal"
    return False, (
        f"opposite thesis blocked: prior {thesis_summary_text(thesis)}; "
        f"new {new_side} needs stronger reversal proof"
    )


def update_current_recommendation(state_game, info, market_context, opportunity=None, status="NO PLAY", reason=""):
    """
    This is for login/dashboard/current-feed status.
    SMS remains BET NOW only, but the state always carries the current recommendation.
    """
    if not ENABLE_CURRENT_RECOMMENDATION_STATE:
        return

    rec = {
        "updated_at": now_local().isoformat(),
        "status": status,
        "reason": reason,
        "score": score_text(info),
        "inning": info.get("inning"),
        "inning_state": info.get("inning_state"),
        "base_out": f"{(info.get('base_state') or {}).get('label', '')}, {info.get('outs')} out(s)",
        "opening_total": (market_context or {}).get("opening_total"),
        "live_total": (market_context or {}).get("live_total"),
        "consensus_total": (market_context or {}).get("consensus_total"),
        "best_available_total": (market_context or {}).get("best_available_total"),
        "best_book": (market_context or {}).get("best_book"),
        "recommended_book": (market_context or {}).get("recommended_book"),
        "recommended_total": (market_context or {}).get("recommended_total"),
        "recommended_price": (market_context or {}).get("recommended_price"),
        "market_confirmation": (market_context or {}).get("market_confirmation_score"),
        "line_velocity": (market_context or {}).get("line_velocity"),
        "book_velocity_summary": (market_context or {}).get("book_velocity_summary"),
        "leading_book": (market_context or {}).get("leading_book"),
        "best_line_age_seconds": (market_context or {}).get("best_line_age_seconds"),
        "market_disagreement": (market_context or {}).get("market_disagreement"),
    }

    if opportunity:
        scores = opportunity.get("scores", {}) or {}
        rec.update({
            "side": opportunity.get("side"),
            "line": opportunity.get("line"),
            "price": opportunity.get("price"),
            "projected_total": opportunity.get("projected_total") or opportunity.get("projection"),
            "edge": opportunity.get("edge"),
            "confidence": opportunity.get("confidence"),
            "model_probability": opportunity.get("model_probability"),
            "break_even_probability": opportunity.get("break_even_probability"),
            "expected_value": opportunity.get("expected_value"),
            "max_entry_line": opportunity.get("max_entry_line") or max_entry_line_for(opportunity),
            "max_entry_price": opportunity.get("max_entry_price") or max_entry_price_for(opportunity.get("side")),
            "app_status": opportunity.get("app_status"),
            "scenario": clean_scenario_label(opportunity.get("scenario", "")),
            "expires_if": expiration_text(info, opportunity),
            "stress": scores.get("pitcher_stress"),
            "contact": scores.get("contact_quality"),
            "p2r": scores.get("pressure_to_runs"),
            "conv": scores.get("run_conversion"),
            "k_env": scores.get("strikeout_environment"),
            "bullpen_lockdown": scores.get("bullpen_lockdown"),
        })

    state_game["current_recommendation"] = rec


def expiration_text(info, opportunity):
    side = str((opportunity or {}).get("side", "")).upper()
    line = safe_float((opportunity or {}).get("line"), 0)
    max_entry = (opportunity or {}).get("max_entry_line") or max_entry_line_for(opportunity)
    if side == "OVER":
        return f"line rises above {max_entry if max_entry is not None else line + 0.5:.1f}, inning ends, bases clear, or signal drops"
    if side == "UNDER":
        return f"line falls below {max_entry if max_entry is not None else line - 0.5:.1f}, traffic appears, bullpen stress rises, or signal drops"
    return "signal changes"


def apply_professional_decision_layer(state_game, info, market_context, opportunity):
    """
    V2.6 professional decision layer.
    The scoring engine can produce candidates, but only this layer can approve BET NOW SMS.

    V3.9.0 adds the business-process loop:
    candidate -> adaptive confidence -> final decision -> master decision log.
    """
    if not opportunity:
        update_current_recommendation(state_game, info, market_context, None, "NO PLAY", "no qualifying opportunity")
        return None, "no opportunity"

    if opportunity.get("action") != "STRIKE":
        update_current_recommendation(state_game, info, market_context, opportunity, "HOLD", "watch-level only; SMS disabled")
        log_shift_decision(state_game, info, market_context, opportunity, "RESEARCH_ONLY", "watch_or_research", "watch-level only; SMS disabled")
        return None, "watch only"

    # V3.2: before the final gate, rewrite the candidate to the best practical app line.
    opportunity = apply_price_adjusted_best_line(info, market_context, opportunity)

    # V3.9.0: apply only sample-disciplined adaptive confidence adjustments.
    opportunity = apply_adaptive_adjustment(opportunity)

    market_context["recommended_book"] = opportunity.get("recommended_book") or market_context.get("recommended_book")
    market_context["recommended_total"] = opportunity.get("recommended_total") or opportunity.get("line")
    market_context["recommended_price"] = opportunity.get("recommended_price") or opportunity.get("price")
    market_context["app_status"] = opportunity.get("app_status") or recommended_app_status(opportunity, market_context)
    if opportunity.get("action") == "NO_PLAY":
        reason = opportunity.get("app_status", "best-line rewrite blocked")
        update_current_recommendation(state_game, info, market_context, opportunity, "NO BET", reason)
        log_shift_decision(state_game, info, market_context, opportunity, "NO_BET", "rejected", reason)
        return None, reason

    approved, reason = v26_final_betnow_gate(state_game, info, market_context, opportunity)
    if not approved:
        update_current_recommendation(state_game, info, market_context, opportunity, "HOLD", reason)
        log_shift_decision(state_game, info, market_context, opportunity, "NO_BET", "rejected", reason)
        print(f"V2.6 BLOCK | {info.get('away')} at {info.get('home')} | {reason}")
        return None, reason

    status = "BET NOW - REVERSAL" if opportunity.get("reversal") else "BET NOW"
    update_current_recommendation(state_game, info, market_context, opportunity, status, reason)
    action = decision_action_from_opportunity(opportunity, approved=True, reason=reason)
    log_shift_decision(state_game, info, market_context, opportunity, action, "accepted", reason)
    return opportunity, reason


def record_active_thesis(state_game, info, opportunity):
    """
    Record the actual recommendation thesis after a BET NOW SMS is sent.
    This drives opposite-side protection and clean login status.
    """
    if not opportunity or opportunity.get("action") != "STRIKE":
        return

    prior = state_game.get("active_thesis")
    state_game["prior_thesis"] = prior if opportunity.get("reversal") else state_game.get("prior_thesis")
    state_game["active_thesis"] = {
        "side": str(opportunity.get("side", "")).upper(),
        "line": opportunity.get("line"),
        "price": opportunity.get("price"),
        "edge": opportunity.get("edge"),
        "projection": opportunity.get("projected_total") or opportunity.get("projection"),
        "confidence": opportunity.get("confidence"),
        "inning": info.get("inning"),
        "inning_float": inning_float(info),
        "score": score_text(info),
        "base_out": f"{(info.get('base_state') or {}).get('label', '')}, {info.get('outs')} out(s)",
        "scenario": clean_scenario_label(opportunity.get("scenario", "")),
        "sent_at": now_local().isoformat(),
        "reversal": bool(opportunity.get("reversal")),
    }


def log_strike_history(info, opportunity, market_context=None):
    """
    Logs each sent STRIKE for later self-learning.
    No API call. Just local CSV memory that the bot can grade and summarize.
    """
    if not ENABLE_STRIKE_HISTORY or opportunity.get("action") != "STRIKE":
        return

    scores = opportunity.get("scores", {})
    market_context = market_context or {}

    strike_id = (
        f"{today()}|{info.get('game_pk', '')}|{opportunity.get('side')}|"
        f"{opportunity.get('line')}|{safe_int(info.get('inning'), 0)}|"
        f"{safe_int(info.get('outs'), 0)}|{int(time.time())}"
    )

    row = {
        "opportunity_id": v4_opportunity_id(info, opportunity, "BET_NOW"),
        "strike_id": strike_id,
        "timestamp": now_local().isoformat(),
        "date": today(),
        "game_key": game_key_from_info(info),
        "game_pk": info.get("game_pk", ""),
        "game": f"{info.get('away')} at {info.get('home')}",
        "side": opportunity.get("side"),
        "line": opportunity.get("line"),
        "price": opportunity.get("price"),
        "opening_total": market_context.get("opening_total"),
        "live_total": market_context.get("live_total", opportunity.get("line")),
        "projected_total": opportunity.get("projection", opportunity.get("projected_total")),
        "edge": opportunity.get("edge"),
        "edge_grade": opportunity.get("edge_grade"),
        "inning": info.get("inning"),
        "inning_state": info.get("inning_state"),
        "outs": info.get("outs"),
        "score": score_text(info),
        "base_out": f"{info.get('base_state', {}).get('label', '')}, {info.get('outs')} out(s)",
        "projection_score": scores.get("projection_score"),
        "confirmation_score": scores.get("confirmation_score"),
        "stress": scores.get("pitcher_stress"),
        "contact": scores.get("contact_quality"),
        "p2r": scores.get("pressure_to_runs"),
        "conv": scores.get("run_conversion"),
        "prev": scores.get("run_prevention"),
        "pred_move": scores.get("predictive_market_move"),
        "threat_index": scores.get("threat_index"),
        "signal_stack": scores.get("signal_stack"),
        "market_lag": scores.get("market_lag"),
        "conv_acceleration": scores.get("conv_acceleration"),
        "k_env": scores.get("strikeout_environment"),
        "bullpen_lockdown": scores.get("bullpen_lockdown"),
        "traffic_conversion": scores.get("traffic_conversion"),
        "hh_eff": scores.get("hard_hit_efficiency"),
        "hh_under": scores.get("hard_hit_under_support"),
        "over_pressure_score": master_score_from_scores("OVER", scores),
        "under_suppression_score": master_score_from_scores("UNDER", scores),
        "market_value_score": master_market_value_score(opportunity, scores),
        "risk_filter_score": master_risk_filter_score(opportunity, info, scores),
        "market_discount": market_context.get("market_discount"),
        "market_discount_score": market_context.get("market_discount_score"),
        "consensus_total": market_context.get("consensus_total"),
        "market_min_total": market_context.get("market_min_total"),
        "market_max_total": market_context.get("market_max_total"),
        "market_disagreement": market_context.get("market_disagreement"),
        "line_velocity": market_context.get("line_velocity"),
        "line_direction": market_context.get("line_direction"),
        "primary_book": market_context.get("primary_book"),
        "best_book": market_context.get("best_book"),
        "best_available_total": market_context.get("best_available_total"),
        "best_available_price": market_context.get("best_available_price"),
        "price_adjusted_best_book": market_context.get("price_adjusted_best_book"),
        "price_adjusted_best_total": market_context.get("price_adjusted_best_total"),
        "price_adjusted_best_price": market_context.get("price_adjusted_best_price"),
        "market_confirmation_score": market_context.get("market_confirmation_score"),
        "book_count": market_context.get("book_count"),
        "first_seen_total": market_context.get("first_seen_total") or market_context.get("opening_total"),
        "true_opening_total": market_context.get("true_opening_total"),
        "recommended_book": opportunity.get("recommended_book") or market_context.get("recommended_book"),
        "recommended_total": opportunity.get("recommended_total") or opportunity.get("line"),
        "recommended_price": opportunity.get("recommended_price") or opportunity.get("price"),
        "best_line_age_seconds": market_context.get("best_line_age_seconds"),
        "best_line_last_update": market_context.get("best_line_last_update"),
        "book_velocity_summary": market_context.get("book_velocity_summary"),
        "leading_book": market_context.get("leading_book"),
        "model_probability": opportunity.get("model_probability"),
        "break_even_probability": opportunity.get("break_even_probability"),
        "expected_value": opportunity.get("expected_value"),
        "app_status": opportunity.get("app_status") or market_context.get("app_status"),
        "max_entry_line": opportunity.get("max_entry_line") or max_entry_line_for(opportunity),
        "max_entry_price": opportunity.get("max_entry_price") or max_entry_price_for(opportunity.get("side")),
        "bet_quality": opportunity.get("bet_quality"),
        "quality_reason": opportunity.get("quality_reason"),
        "lineup_pressure_score": lineup_pocket_score(info),
        "bullpen_context_score": bullpen_context_score(info, scores),
        "confidence_decay_score": opportunity.get("confidence_decay_score"),
        "probability_source": opportunity.get("probability_source"),
        "leading_book_score": leading_book_score(opportunity.get("side"), {"leading_book": market_context.get("leading_book"), "book_velocities": {}}),
        "scenario": opportunity.get("scenario"),
        "action": opportunity.get("action"),
        "market_reaction_profile": market_reaction_profile_from_scores(scores, opportunity.get("scenario")),
        "calculated_risk_tier": opportunity.get("calculated_risk_tier") or calculated_risk_tier(opportunity, market_context),
        "suggested_unit": tier_unit_guidance(opportunity.get("calculated_risk_tier") or calculated_risk_tier(opportunity, market_context)),
        "profile_status": opportunity.get("profile_status"),
        "profile_sample": opportunity.get("profile_sample"),
        "profile_win_pct": opportunity.get("profile_win_pct"),
        "profile_avg_clv": opportunity.get("profile_avg_clv"),
        "profile_confidence_adjustment": opportunity.get("profile_confidence_adjustment"),
        "actual_entry_line": "",
        "actual_entry_price": "",
        "actual_entry_book": "",
        "actual_result": "",
        "final_score": "",
        "final_total": "",
        "result": "PENDING",
        "graded_at": "",
    }
    row["pattern_tags"] = "|".join(pattern_tags_from_row(row))
    bq, qr = classify_bet_quality(row)
    row["bet_quality"] = bq
    row["quality_reason"] = qr

    csv_append_once(STRIKE_HISTORY_FILE, strike_fieldnames(), row)
    actual_template = actual_entry_template_from_strike(row)
    if actual_template:
        csv_append_once(ACTUAL_ENTRY_FILE, actual_entry_fieldnames(), actual_template)
        post_tracking_event("actual_entries", actual_template)
    post_tracking_event("strike_stored", row)
    print(f"SELF-LEARNING STORED STRIKE | {row['game']} | {row['side']} {row['line']} | {row['pattern_tags']}")



def log_clv_snapshot(info, opportunity, current_live_total):
    """
    CLV tracking snapshot.
    This uses odds already fetched during normal polling. No extra credit usage.
    """
    if not ENABLE_CLV_TRACKING or opportunity.get("action") != "STRIKE":
        return

    side = opportunity.get("side")
    alert_line = safe_float(opportunity.get("line"), None)
    current_line = safe_float(current_live_total, None)

    if alert_line is None or current_line is None:
        return

    if side == "OVER":
        clv = round(current_line - alert_line, 1)
        beat_market = clv > 0
    else:
        clv = round(alert_line - current_line, 1)
        beat_market = clv > 0

    fieldnames = [
        "timestamp", "date", "game", "side", "alert_line", "current_line",
        "clv", "beat_market", "inning", "score"
    ]

    row = {
        "timestamp": now_local().isoformat(),
        "date": today(),
        "game": f"{info.get('away')} at {info.get('home')}",
        "side": side,
        "alert_line": alert_line,
        "current_line": current_line,
        "clv": clv,
        "beat_market": beat_market,
        "inning": info.get("inning"),
        "score": f"{info.get('away_runs')}-{info.get('home_runs')}",
    }

    csv_append_once(CLV_HISTORY_FILE, fieldnames, row)
    post_tracking_event("clv_snapshot", row)



def update_active_clv_snapshots(info, live_total):
    """
    V2.4.2 real CLV tracking.
    On every normal odds poll, compare the current live total to any stored STRIKE line
    for this game. This creates a true line-movement trail instead of only a same-moment snapshot.
    No extra Odds API calls; it uses the live_total already fetched in the main loop.
    """
    if not ENABLE_CLV_TRACKING or not ENABLE_CLV_POLL_SNAPSHOTS:
        return
    current_line = safe_float(live_total, None)
    if current_line is None:
        return

    # V3.9.0: keep master decision log CLV fresh using the same already-fetched live total.
    update_decision_log_clv_snapshots(info, live_total)

    rows = csv_read_rows(STRIKE_HISTORY_FILE)
    if not rows:
        return

    state_key = "_last_clv_line"
    changed = False
    for row in rows:
        if row.get("date") != today():
            continue
        if row.get("action") != "STRIKE":
            continue
        if row.get("result") in ["WIN", "LOSS", "PUSH"]:
            continue
        if not (row.get("game_pk") == str(info.get("game_pk")) or row.get("game") == f"{info.get('away')} at {info.get('home')}"):
            continue

        alert_line = safe_float(row.get("line"), None)
        if alert_line is None:
            continue
        last_logged = safe_float(row.get(state_key), None)
        if last_logged is not None and abs(current_line - last_logged) < CLV_SNAPSHOT_MIN_MOVE:
            continue

        side = str(row.get("side", "")).upper()
        if side == "OVER":
            clv = round(current_line - alert_line, 1)
            beat_market = clv > 0
        elif side == "UNDER":
            clv = round(alert_line - current_line, 1)
            beat_market = clv > 0
        else:
            continue

        snap = {
            "timestamp": now_local().isoformat(),
            "date": today(),
            "game": row.get("game"),
            "strike_id": row.get("strike_id"),
            "side": side,
            "alert_line": alert_line,
            "current_line": current_line,
            "clv": clv,
            "beat_market": beat_market,
            "inning": info.get("inning"),
            "score": f"{info.get('away_runs')}-{info.get('home_runs')}",
            "snapshot_type": "poll_update",
        }
        csv_append_once(CLV_HISTORY_FILE, list(snap.keys()), snap)
        post_tracking_event("clv_poll_snapshot", snap)
        row[state_key] = current_line
        changed = True

    if changed:
        # Preserve unknown internal key by writing only known strike fields would drop it,
        # so intentionally do not rewrite strike_history here. CLV snapshots are enough.
        pass



def update_decision_log_clv_snapshots(info, live_total):
    """
    Mirrors CLV poll snapshots into the master decision database so the adaptive
    engine can use closing-line direction, not only win/loss. Uses already-fetched odds.
    """
    if not ENABLE_DECISION_LOG or not ENABLE_CLV_TRACKING:
        return
    current_line = safe_float(live_total, None)
    if current_line is None:
        return
    rows = csv_read_rows(DECISION_LOG_FILE)
    if not rows:
        return
    changed = False
    for row in rows:
        if row.get("date") != today():
            continue
        if row.get("action") not in ["BET_NOW", "TEST_UNIT", "RESEARCH_ONLY", "NO_BET"]:
            continue
        if row.get("result") in ["WIN", "LOSS", "PUSH"]:
            continue
        if not (row.get("game_pk") == str(info.get("game_pk")) or row.get("game") == f"{info.get('away')} at {info.get('home')}"):
            continue
        side = str(row.get("side", "")).upper()
        alert_line = safe_float(row.get("line"), None)
        if side not in ["OVER", "UNDER"] or alert_line is None:
            continue
        if side == "OVER":
            clv = round(current_line - alert_line, 1)
        else:
            clv = round(alert_line - current_line, 1)
        old_clv = safe_float(row.get("clv"), None)
        if old_clv is not None and abs(clv - old_clv) < CLV_SNAPSHOT_MIN_MOVE:
            continue
        row["clv"] = clv
        changed = True
    if changed:
        csv_write_rows(DECISION_LOG_FILE, decision_log_fieldnames(), rows)


def load_state():
    if not os.path.exists(STATE_FILE):
        return {"date": today(), "games": {}, "final_games": {}}
    try:
        with open(STATE_FILE, "r") as f:
            state = json.load(f)
    except Exception:
        return {"date": today(), "games": {}, "final_games": {}}
    if state.get("date") != today():
        return {"date": today(), "games": {}, "final_games": {}}
    state.setdefault("games", {})
    state.setdefault("final_games", {})
    return state


def save_state(state):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)




def v26_signal_snapshot(info, opportunity):
    """Return the core scores used by the final decision engine."""
    scores = (opportunity or {}).get("scores", {}) or {}
    side = str((opportunity or {}).get("side", "")).upper()
    return {
        "side": side,
        "line": safe_float((opportunity or {}).get("line"), 0),
        "edge": abs(safe_float((opportunity or {}).get("edge"), 0)),
        "confidence": safe_int((opportunity or {}).get("confidence"), safe_int(scores.get("confirmation_score"), 0)),
        "projection_score": safe_int(scores.get("projection_score"), 0),
        "confirmation_score": safe_int(scores.get("confirmation_score"), 0),
        "p2r": safe_int(scores.get("pressure_to_runs"), 0),
        "conv": safe_int(scores.get("run_conversion"), 0),
        "stress": safe_int(scores.get("pitcher_stress"), 0),
        "contact": safe_int(scores.get("contact_quality"), 0),
        "k_env": safe_int(scores.get("strikeout_environment"), 0),
        "bp_lock": safe_int(scores.get("bullpen_lockdown"), 0),
        "traffic_conv": safe_int(scores.get("traffic_conversion"), 0),
        "hh_eff": safe_int(scores.get("hard_hit_efficiency"), 0),
        "value": master_market_value_score(opportunity or {}, scores),
        "risk": master_risk_filter_score(opportunity or {}, info or {}, scores),
    }


def market_chase_block_reason(info, market_context, opportunity):
    """
    V3.1 do-not-chase protection.
    If the live total already moved heavily from the opener in the same direction
    as the proposed bet, require elite baseball edge and elite market confirmation.
    """
    if not ENABLE_MARKET_INTELLIGENCE or not opportunity:
        return None
    side = str(opportunity.get("side", "")).upper()
    opening = safe_float((market_context or {}).get("opening_total"), None)
    live = safe_float((market_context or {}).get("live_total"), None)
    if opening is None or live is None:
        return None
    move = live - opening
    chase = (side == "OVER" and move >= DO_NOT_CHASE_MOVE_FROM_OPEN) or (side == "UNDER" and move <= -DO_NOT_CHASE_MOVE_FROM_OPEN)
    if not chase:
        return None
    scores = (opportunity or {}).get("scores", {}) or {}
    confidence = safe_int(opportunity.get("confidence"), 0)
    edge = abs(safe_float(opportunity.get("edge"), 0))
    market_conf = safe_int((market_context or {}).get("market_confirmation_score"), 50)
    projection = safe_int(scores.get("projection_score"), 0)
    confirmation = safe_int(scores.get("confirmation_score"), 0)
    elite = (
        confidence >= DO_NOT_CHASE_MIN_CONFIDENCE
        and edge >= DO_NOT_CHASE_MIN_EDGE
        and market_conf >= DO_NOT_CHASE_MIN_MARKET_CONFIRMATION
        and projection >= 85
        and confirmation >= 85
    )
    if not elite:
        return f"do-not-chase: live total moved {move:+.1f} from open; elite confirmation required"
    return None


def v26_final_betnow_gate(state_game, info, market_context, opportunity):
    """
    The one clean final gate for SMS.
    The model can still create candidates, but this function decides whether a text is allowed.
    """
    if not ENABLE_V26_SINGLE_DECISION_ENGINE:
        return True, "legacy engine enabled"

    if not opportunity or opportunity.get("action") != "STRIKE":
        return False, "no BET NOW candidate"

    if V26_REJECT_STALE_STATUS and str(info.get("status", "")).lower() != "live":
        return False, "game is not live"

    recommended_book = opportunity.get("recommended_book") or market_context.get("recommended_book") or market_context.get("price_adjusted_best_book") or market_context.get("best_book")
    if recommended_book and is_ignored_recommendation_book(recommended_book):
        return False, f"ignored book blocked from final STRIKE: {recommended_book}"
    if REQUIRE_PLAYABLE_BOOK_FOR_STRIKE and USER_PLAYABLE_BOOKS and (not recommended_book or not is_user_playable_book(recommended_book)):
        return False, "no playable BetMGM/user-book line available for final STRIKE"

    reaction_ok, reaction_reason = market_reaction_side_gate(opportunity.get("side"), opportunity.get("edge"), opportunity.get("scores", {}), info)
    if not reaction_ok:
        return False, reaction_reason

    market_first_block = v33_market_first_block_reason(info, market_context, opportunity)
    if market_first_block:
        return False, market_first_block

    baseball_quality_block = v33_baseball_quality_block_reason(info, opportunity)
    if baseball_quality_block:
        return False, baseball_quality_block

    decay = confidence_decay_score(state_game, info, opportunity)
    opportunity["confidence_decay_score"] = decay
    if decay >= CONFIDENCE_DECAY_BLOCK_LEVEL:
        return False, f"confidence-decay block: stale setup risk {decay}"

    block = professional_block_reason(info, opportunity)
    if block:
        return False, block

    chase_block = market_chase_block_reason(info, market_context, opportunity)
    if chase_block:
        return False, chase_block

    # V3.2 app-practical gates: price, stale best line, and expected value.
    if not price_ok(opportunity.get("price"), abs(safe_float(opportunity.get("edge"), 0))):
        return False, "price not playable in app range"
    age = safe_float((market_context or {}).get("best_line_age_seconds"), None)
    if age is not None and age > MAX_BEST_LINE_AGE_SECONDS:
        return False, f"best app line is stale ({int(age)}s old)"
    ev_info = expected_value_per_unit(opportunity.get("side"), opportunity.get("edge"), opportunity.get("price"))
    opportunity.update(ev_info)
    if REQUIRE_POSITIVE_EV and ev_info.get("expected_value") is not None and ev_info.get("expected_value") < MIN_EXPECTED_VALUE:
        return False, f"EV {ev_info.get('expected_value'):+.3f} below minimum {MIN_EXPECTED_VALUE:+.3f}"

    snap = v26_signal_snapshot(info, opportunity)
    profile = market_reaction_profile_from_scores((opportunity or {}).get("scores", {}), (opportunity or {}).get("scenario"))

    # V3.7.7: NEUTRAL_MARKET is not deleted; it remains a research bucket.
    # It is no longer allowed to become a BET NOW text because recent results
    # showed it losing materially compared with true reaction profiles.
    if ENABLE_NEUTRAL_MARKET_WATCH_ONLY and profile == "NEUTRAL_MARKET":
        opportunity["neutral_market_watch_only"] = True
        opportunity["neutral_market_watch_reason"] = NEUTRAL_MARKET_WATCH_REASON
        return False, NEUTRAL_MARKET_WATCH_REASON

    # V3.7.9: Discounted OVER remains live, but weak late-game versions are watch/research only.
    late_disc_block, late_disc_reason = should_block_late_discounted_over(info, opportunity, opportunity.get("scores", {}))
    if late_disc_block:
        log_profile_research_candidate(info, opportunity, late_disc_reason, market_context, promoted=False)
        return False, late_disc_reason

    profile_min_conf = profile_promotion_min_confidence(profile)
    if snap["confidence"] < profile_min_conf:
        return False, f"confidence {snap['confidence']} below profile minimum {profile_min_conf} for {profile}"
    if snap["edge"] < V26_MIN_BETNOW_EDGE:
        return False, f"edge {snap['edge']} below V2.6 minimum {V26_MIN_BETNOW_EDGE}"
    if snap["value"] < V26_MIN_VALUE_SCORE:
        return False, f"value score {snap['value']} below V2.6 minimum {V26_MIN_VALUE_SCORE}"
    max_risk = V26_EXTREME_MAX_RISK_SCORE if snap["line"] >= MAX_EXTREME_TOTAL_STRIKE_LINE else V26_MAX_RISK_SCORE
    if snap["risk"] > max_risk:
        return False, f"risk score {snap['risk']} exceeds V2.6 max {max_risk}"

    if ENABLE_MARKET_INTELLIGENCE and REQUIRE_MARKET_CONFIRMATION_WHEN_AVAILABLE:
        book_count = safe_int((market_context or {}).get("book_count"), 0)
        market_conf = safe_int((market_context or {}).get("market_confirmation_score"), 50)
        if book_count >= MIN_MARKET_BOOKS_FOR_CONFIRMATION:
            min_market = MIN_MARKET_CONFIRMATION_EXTREME if snap["line"] >= MAX_EXTREME_TOTAL_STRIKE_LINE else MIN_MARKET_CONFIRMATION_SCORE
            if market_conf < min_market:
                return False, f"market confirmation {market_conf} below required {min_market}"

    thesis = state_game.get("active_thesis") or {}
    old_side = str(thesis.get("side", "")).upper()
    new_side = snap["side"]

    sent_strikes = [a for a in state_game.get("alerts", []) if a.get("action") == "STRIKE"]
    if V26_ONE_BET_NOW_PER_GAME and sent_strikes and not opportunity.get("reversal"):
        if old_side and old_side != new_side and V26_ALLOW_REVERSAL_ONLY:
            ok, reason = reversal_allowed(state_game, info, opportunity)
            if not ok:
                return False, reason
            opportunity["reversal"] = True
            opportunity["previous_thesis"] = dict(thesis)
            return True, "approved V2.6 thesis reversal"
        return False, "one BET NOW already sent for this game"

    if old_side and old_side != new_side and V26_ALLOW_REVERSAL_ONLY:
        ok, reason = reversal_allowed(state_game, info, opportunity)
        if not ok:
            return False, reason
        opportunity["reversal"] = True
        opportunity["previous_thesis"] = dict(thesis)
        return True, "approved V2.6 thesis reversal"

    return True, "approved V2.6 BET NOW"


def format_bet_now_sms(label, info, market_context, opportunity):
    """
    Build the SMS directly from live objects instead of parsing the long alert text.
    This is safer and makes the recommendation clear at login/SMS time.
    """
    scores = (opportunity or {}).get("scores", {}) or {}
    side = str(opportunity.get("side", "")).upper()
    line = opportunity.get("line")
    price = opportunity.get("price")
    price_text = price if price is not None else "N/A"
    proj = opportunity.get("projected_total") or opportunity.get("projection")
    edge = safe_float(opportunity.get("edge"), 0)
    edge_sign = "+" if edge > 0 else ""
    title = "🔄 SHIFT REVERSAL — BET NOW" if opportunity.get("reversal") else "🚨 SHIFT STRIKE — BET NOW"

    signal_parts = []
    if side == "OVER":
        for key, label_name in [
            ("pitcher_stress", "Stress"),
            ("pressure_to_runs", "P2R"),
            ("run_conversion", "Conv"),
            ("traffic_conversion", "Traffic"),
            ("market_lag", "Lag"),
        ]:
            val = safe_int(scores.get(key), 0)
            if val:
                signal_parts.append(f"{label_name} {val}/100")
    else:
        for key, label_name in [
            ("run_prevention", "Prev"),
            ("strikeout_environment", "KEnv"),
            ("bullpen_lockdown", "BPLock"),
            ("hard_hit_under_support", "HHUnder"),
            ("under_environment", "UnderEnv"),
        ]:
            val = safe_int(scores.get(key), 0)
            if val:
                signal_parts.append(f"{label_name} {val}/100")

    base_label = (info.get("base_state") or {}).get("label", "")
    prev = opportunity.get("previous_thesis") or {}
    app_status = opportunity.get("app_status") or market_context.get("app_status") or recommended_app_status(opportunity, market_context)
    ev = opportunity.get("expected_value")
    mp = opportunity.get("model_probability")
    bp = opportunity.get("break_even_probability")
    prob_src = opportunity.get("probability_source")
    lineup_score = lineup_pocket_score(info)
    bullpen_score = bullpen_context_score(info, scores)
    decay_score = opportunity.get("confidence_decay_score")
    book = opportunity.get("recommended_book") or market_context.get("recommended_book") or market_context.get("price_adjusted_best_book") or "Configured app"
    if book and is_ignored_recommendation_book(book):
        book = "BLOCKED_BOOK_FILTER_ERROR"
    first_seen = market_context.get("first_seen_total") or market_context.get("opening_total")
    true_open = market_context.get("true_opening_total") or "unknown"
    age = market_context.get("best_line_age_seconds")
    age_text = f" | Age {age}s" if age is not None else ""
    max_entry = opportunity.get("max_entry_line") or max_entry_line_for(opportunity)
    max_price = opportunity.get("max_entry_price") or max_entry_price_for(side)
    leading = market_context.get("leading_book")
    leading_text = f" | Lead {leading}" if leading else ""
    lines = [
        title,
        label,
        "",
        f"APP STATUS: {app_status}",
        f"PLAY: {side} {line} ({price_text}) at {book}",
        f"MAX ENTRY: {side} {max_entry} or better | Max price {max_price}",
        f"FirstSeen/TrueOpen/Live/Proj: {first_seen}/{true_open}/{market_context.get('live_total')}/{proj}",
        f"Market: Cons {market_context.get('consensus_total')} | Vel {market_context.get('line_velocity')}{leading_text} | MktConf {market_context.get('market_confirmation_score')}{age_text}",
        f"EV: {ev if ev is not None else 'N/A'} | Model {mp if mp is not None else 'N/A'} | BE {bp if bp is not None else 'N/A'}",
        f"Profile: {market_reaction_profile_from_scores(scores, opportunity.get('scenario'))} | Tier {opportunity.get('calculated_risk_tier') or calculated_risk_tier(opportunity, market_context)} | {tier_unit_guidance(opportunity.get('calculated_risk_tier') or calculated_risk_tier(opportunity, market_context))}",
        f"Promotion: {scores.get('profile_promotion_reason', 'standard gate')}",
        f"Intel: Lineup {lineup_score}/100 | BullpenRisk {bullpen_score}/100 | Decay {decay_score if decay_score is not None else 0}/100 | Prob {prob_src or 'formula'}",
        f"Edge: {edge_sign}{edge} runs | Conf: {opportunity.get('confidence', 'N/A')}/100",
        f"Score: {info.get('away_runs')}-{info.get('home_runs')} | {info.get('inning_state')} {info.get('inning')}",
        f"Base/Out: {base_label}, {info.get('outs')} out(s)",
    ]
    if prev:
        lines.append(f"Previous: {prev.get('side')} {prev.get('line')} at inning {prev.get('inning')}")
    if signal_parts:
        lines.append("Signals: " + " | ".join(signal_parts[:4]))
    lines.append("Expires if: " + expiration_text(info, opportunity))
    lines.append("")
    lines.append("BET NOW")
    text = "\n".join(lines)
    if len(text) > MAX_SHORT_SMS_CHARS:
        text = text[:MAX_SHORT_SMS_CHARS - 20].rstrip() + "\n[Trimmed]"
    return text


def compact_sms_message(msg, max_chars=MAX_SMS_CHARS):
    """
    Twilio hard rejects long message bodies.
    This keeps the FULL alert printed in Railway logs while sending
    a compact SMS that stays under the limit.
    """
    if not msg:
        return msg

    if len(msg) <= max_chars:
        return msg

    lines = [ln.rstrip() for ln in msg.splitlines() if ln.strip()]
    keep_prefixes = (
        "SHIFT",
        "PLAY:",
        "Market:",
        "Scenario:",
        "Instruction:",
        "Open:",
        "Live Line:",
        "Proj:",
        "Edge:",
        "Entry Guidance:",
        "Score:",
        "Inning:",
        "Base/Out:",
        "Projection Score:",
        "Confirmation Score:",
        "Need Runs:",
        "Action Window:",
        "Confidence:",
        "CIP:",
        "RO:",
        "Stress:",
        "Dom:",
        "Contact:",
        "Lineup:",
        "Conv:",
        "P2R:",
        "PreOver:",
        "OVal:",
        "Prev:",
        "PredMove:",
    )

    compact = []
    # Always keep the header and matchup block.
    for ln in lines[:8]:
        compact.append(ln)

    # Keep key scoring/decision lines.
    for ln in lines[8:]:
        if ln.startswith(keep_prefixes):
            compact.append(ln)

    # Keep the strongest "why" bullets only.
    why_lines = [ln for ln in lines if ln.startswith("•")]
    if why_lines:
        compact.append("Why:")
        compact.extend(why_lines[:5])

    # Keep only the first pitch-type flag line if present.
    for idx, ln in enumerate(lines):
        if ln.startswith("Pitch-Type Flags:"):
            compact.append("Pitch-Type Flags:")
            if idx + 1 < len(lines):
                compact.append(lines[idx + 1])
            break

    text = "\n".join(compact)

    if len(text) <= max_chars:
        return text

    # Final hard cap.
    return text[: max_chars - 90].rstrip() + "\n\n[Trimmed for SMS. Full alert in Railway logs.]"




def alert_action_from_message(msg):
    """
    Technical SMS classifier only.
    Reversals are BET NOW recommendations even if the first line does not contain the word STRIKE.
    """
    first_line = (msg.splitlines()[0] if msg else "").upper()
    body = (msg or "").upper()
    if "WATCH" in first_line:
        return "WATCH"
    if "SHIFT REVERSAL" in first_line and "BET NOW" in body:
        return "STRIKE"
    if "STRIKE" in first_line and "BET NOW" in body:
        return "STRIKE"
    if "BET NOW" in first_line:
        return "STRIKE"
    return "UNKNOWN"


def extract_alert_value(msg, label):
    """
    Pulls simple values out of the full alert body for compact SMS.
    """
    for line in msg.splitlines():
        line = line.strip()
        if line.startswith(label):
            return line.split(":", 1)[1].strip() if ":" in line else line.replace(label, "").strip()
    return ""


def first_nonempty_after_label(msg, label):
    lines = [ln.rstrip() for ln in msg.splitlines()]
    for i, ln in enumerate(lines):
        if ln.strip().startswith(label):
            for j in range(i + 1, min(i + 4, len(lines))):
                if lines[j].strip():
                    return lines[j].strip()
    return ""


def compact_strike_sms(msg):
    """
    Safe fallback only.
    V2.6 approved SMS should come from format_bet_now_sms(label, info, market_context, opportunity),
    not from parsing the long Railway alert. This fallback avoids runtime errors if an older path calls it.
    """
    if not msg:
        return ""
    lines = [ln.strip() for ln in msg.splitlines() if ln.strip()]
    header = lines[0] if lines else "🚨 SHIFT STRIKE — BET NOW"
    matchup = lines[1] if len(lines) > 1 else ""
    play = first_nonempty_after_label(msg, "PLAY:") or extract_alert_value(msg, "Live Line")
    score = extract_alert_value(msg, "Score")
    inning = extract_alert_value(msg, "Inning")
    base_out = extract_alert_value(msg, "Base/Out")
    proj = extract_alert_value(msg, "Proj")
    edge = extract_alert_value(msg, "Edge")

    compact = [header, matchup, ""]
    if play:
        compact.append(f"PLAY: {play}")
    if proj or edge:
        compact.append(f"Proj: {proj} | Edge: {edge}".strip())
    if score or inning:
        compact.append(f"Score: {score} | {inning}".strip())
    if base_out:
        compact.append(f"Base/Out: {base_out}")
    compact.extend(["", "BET NOW"])
    text = "\n".join([ln for ln in compact if ln is not None])
    if len(text) > MAX_SHORT_SMS_CHARS:
        text = text[:MAX_SHORT_SMS_CHARS - 20].rstrip() + "\n[Trimmed]"
    return text


def should_send_sms(msg, sms_body=None):
    """
    Final technical send guard.
    The betting decision already happened in v26_final_betnow_gate().
    If an approved V2.6 sms_body is supplied, allow both STRIKE and SHIFT REVERSAL texts.
    """
    if not SEND_ONLY_STRIKE_SMS:
        return True
    body = (sms_body or msg or "").upper()
    if "WATCH" in (msg.splitlines()[0].upper() if msg else ""):
        return False
    return "BET NOW" in body and ("STRIKE" in body or "SHIFT REVERSAL" in body or "PLAY:" in body)


def send_text(msg, sms_body=None):
    # Full alert always stays in Railway logs.
    print("\n" + msg + "\n")

    if not should_send_sms(msg, sms_body=sms_body):
        print("TEXT NOT SENT: non-BET NOW alert logged only.")
        return

    sms_body = sms_body or compact_strike_sms(msg)
    if len(sms_body) > MAX_SHORT_SMS_CHARS:
        sms_body = sms_body[:MAX_SHORT_SMS_CHARS - 40].rstrip() + "\n[Trimmed]"

    print(f"SMS LENGTH: {len(sms_body)} chars")

    if not all([TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER, ALERT_TO_NUMBER]):
        print("TEXT NOT SENT: Missing Twilio variables.")
        return
    try:
        client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
        client.messages.create(body=sms_body, from_=TWILIO_FROM_NUMBER, to=ALERT_TO_NUMBER)
        print("TEXT SENT SUCCESSFULLY")
    except Exception as e:
        print("TEXT ERROR:", repr(e))


def realtime_data_status():
    return {
        "live_game_provider": LIVE_GAME_PROVIDER,
        "odds_provider": ODDS_PROVIDER,
        "premium_data_provider": PREMIUM_DATA_PROVIDER,
        "mlb_stats_api_enabled": ENABLE_MLB_STATS_API_CONTEXT,
        "sportsdataio_configured": bool(SPORTSDATAIO_KEY),
        "opticodds_configured": bool(OPTICODDS_KEY),
        "sportradar_configured": bool(SPORTRADAR_KEY),
    }


def get_schedule():
    """
    V3.5 live-game adapter. Default source is MLB Stats API.
    This endpoint requires no API key and supplies the daily MLB slate, status, start time, and final scores.
    """
    if LIVE_GAME_PROVIDER not in ["mlb_stats_api", "mlb", "statsapi"]:
        print(f"LIVE GAME PROVIDER '{LIVE_GAME_PROVIDER}' not implemented; falling back to MLB Stats API.")

    if not ENABLE_MLB_STATS_API_CONTEXT:
        print("MLB STATS API CONTEXT DISABLED")
        return []

    url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today()}&hydrate=probablePitcher,team"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            print("MLB SCHEDULE API ERROR:", r.status_code, r.text[:200])
            return []
        data = r.json()
    except Exception as e:
        print("MLB SCHEDULE API EXCEPTION:", repr(e))
        return []

    games = []
    for d in data.get("dates", []):
        games.extend(d.get("games", []))
    return games


def get_feed(game_pk):
    """
    V3.5 live feed adapter. Pulls MLB Stats API live feed for real-time baseball context:
    inning, score, outs, base state, current batter, pitcher, lineups, boxscore, and play-by-play.
    """
    if not ENABLE_MLB_STATS_API_CONTEXT:
        return {}
    url = f"https://statsapi.mlb.com/api/v1.1/game/{game_pk}/feed/live"
    try:
        r = requests.get(url, timeout=15)
        if r.status_code != 200:
            print("MLB LIVE FEED API ERROR:", game_pk, r.status_code, r.text[:200])
            return {}
        return r.json()
    except Exception as e:
        print("MLB LIVE FEED API EXCEPTION:", game_pk, repr(e))
        return {}


def get_odds():
    if not ODDS_API_KEY:
        print("ODDS API KEY MISSING")
        return []

    url = "https://api.the-odds-api.com/v4/sports/baseball_mlb/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "us",
        "markets": ODDS_MARKETS,
        "oddsFormat": "american",
    }

    try:
        r = requests.get(url, params=params, timeout=15)
        if r.status_code != 200:
            print("ODDS API ERROR:", r.status_code, r.text)
            return []
        data = r.json()
        print(f"ODDS EVENTS RETURNED: {len(data)} | Markets: {ODDS_MARKETS} | Credit-smart: no extra CLV/WATCH calls")
        return data
    except Exception as e:
        print("ODDS API EXCEPTION:", repr(e))
        return []


def parse_start_time(game):
    raw = game.get("gameDate")
    if not raw:
        return None
    return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(TZ)


def should_fetch_feed(start_time):
    if not start_time:
        return True
    minutes_until = (start_time - now_local()).total_seconds() / 60
    return minutes_until <= PREGAME_WINDOW_MINUTES


def get_current_base_state(linescore):
    offense = linescore.get("offense", {})
    first = bool(offense.get("first"))
    second = bool(offense.get("second"))
    third = bool(offense.get("third"))

    if first and second and third:
        label = "Bases loaded"
    elif first and second:
        label = "1st and 2nd"
    elif first and third:
        label = "1st and 3rd"
    elif second and third:
        label = "2nd and 3rd"
    elif first:
        label = "Runner on 1st"
    elif second:
        label = "Runner on 2nd"
    elif third:
        label = "Runner on 3rd"
    else:
        label = "Bases empty"

    return {
        "first": first,
        "second": second,
        "third": third,
        "runners_on": int(first) + int(second) + int(third),
        "label": label,
    }


def base_out_expectancy_score(base_state, outs):
    first = base_state["first"]
    second = base_state["second"]
    third = base_state["third"]
    runners = base_state["runners_on"]

    if runners == 0:
        raw = 4
    elif first and not second and not third:
        raw = 15
    elif second and not first and not third:
        raw = 30
    elif third and not first and not second:
        raw = 40
    elif first and second and not third:
        raw = 42
    elif first and third and not second:
        raw = 55
    elif second and third and not first:
        raw = 68
    elif first and second and third:
        raw = 82
    else:
        raw = 0

    if outs == 0:
        raw *= 1.15
    elif outs == 2:
        raw *= 0.50

    return round(clamp(raw))


def parse_game(feed, schedule_game):
    gd = feed.get("gameData", {})
    ld = feed.get("liveData", {})
    linescore = ld.get("linescore", {})
    current_play = ld.get("plays", {}).get("currentPlay", {})
    matchup = current_play.get("matchup", {})

    home = gd.get("teams", {}).get("home", {}).get("name", "")
    away = gd.get("teams", {}).get("away", {}).get("name", "")
    status = gd.get("status", {}).get("abstractGameState", "")

    defense = linescore.get("defense", {})
    pitcher = defense.get("pitcher", {})
    inning_state = linescore.get("inningState", "")

    if inning_state == "Top":
        batting_side = "away"
    elif inning_state == "Bottom":
        batting_side = "home"
    else:
        batting_side = None

    base_state = get_current_base_state(linescore)
    outs = safe_int(linescore.get("outs", 0), 0)
    inning = safe_int(linescore.get("currentInning", 1), 1)

    away_runs = linescore.get("teams", {}).get("away", {}).get("runs", 0) or 0
    home_runs = linescore.get("teams", {}).get("home", {}).get("runs", 0) or 0


    # V3.4 lineup-pocket snapshot. Uses batting order if MLB feed exposes it.
    def _batting_order_snapshot(side):
        try:
            team_box = ld.get("boxscore", {}).get("teams", {}).get(side or "", {})
            order = [str(x) for x in (team_box.get("battingOrder") or [])]
            players = team_box.get("players", {}) or {}
            current_id = str((matchup.get("batter") or {}).get("id") or "")
            if not order or not current_id or current_id not in order:
                return [], None
            idx = order.index(current_id)
            pocket = []
            for offset in range(3):
                pid = order[(idx + offset) % len(order)]
                pdata = players.get("ID" + pid, {}) or {}
                person = pdata.get("person", {}) or {}
                slot = ((idx + offset) % 9) + 1
                pocket.append({"id": pid, "name": person.get("fullName", "Unknown"), "slot": slot})
            return pocket, ((idx % 9) + 1)
        except Exception:
            return [], None

    next_batter_pocket, current_batter_order_slot = _batting_order_snapshot(batting_side)

    return {
        "game_pk": str(schedule_game.get("gamePk", "")),
        "home": home,
        "away": away,
        "status": status,
        "start_time": parse_start_time(schedule_game),
        "inning": inning,
        "inning_state": inning_state,
        "outs": outs,
        "home_runs": home_runs,
        "away_runs": away_runs,
        "total_runs": home_runs + away_runs,
        "base_state": base_state,
        "runners_on": base_state["runners_on"],
        "base_out_pressure": base_out_expectancy_score(base_state, outs),
        "batting_side": batting_side,
        "current_batter_id": matchup.get("batter", {}).get("id"),
        "current_batter_name": matchup.get("batter", {}).get("fullName", "Unknown"),
        "current_batter_hand": matchup.get("batSide", {}).get("code"),
        "current_batter_order_slot": current_batter_order_slot,
        "next_batter_pocket": next_batter_pocket,
        "lineup_pressure_score": lineup_pocket_score({"next_batter_pocket": next_batter_pocket, "current_batter_order_slot": current_batter_order_slot}),
        "pitcher_name": pitcher.get("fullName", "Unknown"),
        "pitcher_id": pitcher.get("id"),
        "pitcher_hand": matchup.get("pitchHand", {}).get("code"),
        "home_probable_pitcher": (gd.get("probablePitchers", {}).get("home", {}) or {}).get("fullName", ""),
        "away_probable_pitcher": (gd.get("probablePitchers", {}).get("away", {}) or {}).get("fullName", ""),
        "real_time_provider": LIVE_GAME_PROVIDER,
    }


def innings_remaining_estimate(info):
    inning = safe_int(info["inning"], 1)
    state = info["inning_state"]
    half_innings_played = max(0, (inning - 1) * 2 + (0 if state == "Top" else 1))
    total_scheduled_halves = 18

    if inning >= 9 and info["home_runs"] > info["away_runs"]:
        total_scheduled_halves = 17

    halves_remaining = max(0, total_scheduled_halves - half_innings_played)
    return halves_remaining / 2.0


def get_player_hand(feed, player_id, hand_type):
    if not player_id:
        return None
    player = feed.get("gameData", {}).get("players", {}).get(f"ID{player_id}", {})
    if hand_type == "bat":
        return player.get("batSide", {}).get("code")
    if hand_type == "pitch":
        return player.get("pitchHand", {}).get("code")
    return None


def get_batting_order(feed, side):
    if side not in ["home", "away"]:
        return []

    box_team = feed.get("liveData", {}).get("boxscore", {}).get("teams", {}).get(side, {})
    players = box_team.get("players", {})
    order = []

    for pdata in players.values():
        bo = pdata.get("battingOrder")
        person = pdata.get("person", {})
        if not bo:
            continue
        try:
            slot = int(str(bo)[0])
        except Exception:
            continue
        pid = person.get("id")
        order.append({
            "slot": slot,
            "id": pid,
            "name": person.get("fullName", "Unknown"),
            "hand": get_player_hand(feed, pid, "bat"),
        })

    seen = set()
    cleaned = []
    for h in sorted(order, key=lambda x: (x["slot"], x["name"])):
        key = (h["slot"], h["id"])
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(h)
    return cleaned


def upcoming_hitters(feed, info, count=4):
    order = get_batting_order(feed, info.get("batting_side"))
    if not order:
        return []

    current_id = info.get("current_batter_id")
    current_index = None

    for i, h in enumerate(order):
        if h["id"] == current_id:
            current_index = i
            break

    if current_index is None:
        return order[:count]

    return [order[(current_index + step) % len(order)] for step in range(count)]


def lineup_slot_value(slot):
    if slot in [1, 2, 3]:
        return 22
    if slot in [4, 5]:
        return 20
    if slot == 6:
        return 12
    if slot == 7:
        return 7
    return 4


def platoon_value(pitcher_hand, batter_hand):
    if not pitcher_hand or not batter_hand:
        return 0
    if batter_hand == "S":
        return 8
    if pitcher_hand == "L" and batter_hand == "R":
        return 10
    if pitcher_hand == "R" and batter_hand == "L":
        return 10
    return 2


def lineup_pressure_score(info, hitters):
    if not hitters:
        return 0

    score = 0
    pitcher_hand = info.get("pitcher_hand")

    for idx, h in enumerate(hitters):
        weight = 1.0 if idx == 0 else 0.70 if idx == 1 else 0.50 if idx == 2 else 0.35
        score += lineup_slot_value(h["slot"]) * weight
        score += platoon_value(pitcher_hand, h.get("hand")) * weight

    base_pressure = info["base_out_pressure"]
    if base_pressure >= 75:
        score *= 1.35
    elif base_pressure >= 55:
        score *= 1.20
    elif base_pressure >= 30:
        score *= 1.08

    return round(clamp(score))


def format_hitters(hitters):
    if not hitters:
        return "Unknown"
    return ", ".join([f"{h['slot']}-{h['name']}({h.get('hand') or '?'})" for h in hitters])


def pitcher_box(feed, pitcher_id):
    empty = {
        "pitch_count": 0,
        "walks": 0,
        "strikeouts": 0,
        "runs": 0,
        "hits": 0,
        "innings": 0.0,
        "outs_recorded": 0,
        "batters_faced": 0,
        "hbp": 0,
        "home_runs": 0,
    }

    if not pitcher_id:
        return empty

    box = feed.get("liveData", {}).get("boxscore", {})
    pid = f"ID{pitcher_id}"

    for side in ["home", "away"]:
        players = box.get("teams", {}).get(side, {}).get("players", {})
        if pid in players:
            p = players[pid].get("stats", {}).get("pitching", {})
            innings_raw = str(p.get("inningsPitched", "0"))
            innings = safe_float(innings_raw.replace(".1", ".33").replace(".2", ".67"), 0)

            outs = int(math.floor(innings)) * 3
            if ".1" in innings_raw:
                outs += 1
            elif ".2" in innings_raw:
                outs += 2

            return {
                "pitch_count": safe_int(p.get("numberOfPitches")),
                "walks": safe_int(p.get("baseOnBalls")),
                "strikeouts": safe_int(p.get("strikeOuts")),
                "runs": safe_int(p.get("runs")),
                "hits": safe_int(p.get("hits")),
                "innings": innings,
                "outs_recorded": outs,
                "batters_faced": safe_int(p.get("battersFaced")),
                "hbp": safe_int(p.get("hitBatsmen")),
                "home_runs": safe_int(p.get("homeRuns")),
            }

    return empty


def extract_recent_sequence(feed, pitcher_id=None, last_n=14):
    plays = feed.get("liveData", {}).get("plays", {}).get("allPlays", [])
    if pitcher_id:
        plays = [p for p in plays if p.get("matchup", {}).get("pitcher", {}).get("id") == pitcher_id]
    return plays[-last_n:]


def event_text(play):
    return (play.get("result", {}).get("event") or "").lower()


def play_is_baserunner(play):
    event = event_text(play)
    return any(x in event for x in [
        "single", "double", "triple", "home run", "walk", "hit by pitch",
        "fielding error", "catcher interference"
    ])


def play_is_hit(play):
    event = event_text(play)
    return any(x in event for x in ["single", "double", "triple", "home run"])


def play_is_walk(play):
    return "walk" in event_text(play)


def play_is_strikeout(play):
    return "strikeout" in event_text(play)


def max_consecutive_baserunners(plays):
    best = 0
    cur = 0
    for p in plays:
        if play_is_baserunner(p):
            cur += 1
            best = max(best, cur)
        else:
            cur = 0
    return best


def traffic_metrics(feed, pitcher_id):
    recent = extract_recent_sequence(feed, pitcher_id, last_n=14)
    return {
        "recent_baserunners": sum(1 for p in recent if play_is_baserunner(p)),
        "recent_hits": sum(1 for p in recent if play_is_hit(p)),
        "recent_walks": sum(1 for p in recent if play_is_walk(p)),
        "recent_strikeouts": sum(1 for p in recent if play_is_strikeout(p)),
        "consecutive_baserunners": max_consecutive_baserunners(recent),
    }


def live_statcast_quality(feed, pitcher_id):
    summary = {
        "total_pitches": 0,
        "strike_pct": 0,
        "whiff_pct": 0,
        "zone_pct": 0,
        "first_pitch_strike_pct": 0,
        "csw_pct": 0,
        "hard_hit": 0,
        "barrels": 0,
        "balls_in_play": 0,
        "avg_ev": 0,
        "max_ev": 0,
        "ev_trend": 0,
        "recent_avg_ev": 0,
        "early_avg_ev": 0,
        "velo_drop": 0,
        "spin_drop": 0,
        "movement_drop": 0,
        "release_drift": 0,
        "pitch_types": {},
    }

    if not pitcher_id:
        return summary

    total_pitches = 0
    strikes = 0
    called_or_swinging = 0
    swings = 0
    whiffs = 0
    zone_pitches = 0
    first_pitch_total = 0
    first_pitch_strikes = 0

    all_velos = []
    all_spins = []
    all_breaks = []
    release_points = []
    exit_velos = []
    pitch_types = {}

    for play in feed.get("liveData", {}).get("plays", {}).get("allPlays", []):
        pitcher = play.get("matchup", {}).get("pitcher", {})
        if pitcher.get("id") != pitcher_id:
            continue

        play_pitch_number = 0

        for event in play.get("playEvents", []):
            details = event.get("details", {})
            pitch_data = event.get("pitchData", {})
            hit_data = event.get("hitData", {})

            pitch_type = (
                details.get("type", {}).get("description")
                or details.get("type", {}).get("code")
                or "Unknown"
            )

            if pitch_type not in pitch_types:
                pitch_types[pitch_type] = {
                    "count": 0,
                    "velos": [],
                    "spins": [],
                    "breaks": [],
                    "hard_hit": 0,
                    "barrels": 0,
                    "bip": 0,
                    "whiffs": 0,
                    "swings": 0,
                    "strikes": 0,
                }

            if event.get("isPitch"):
                play_pitch_number += 1
                total_pitches += 1
                pitch_types[pitch_type]["count"] += 1

                code = details.get("code", "")
                desc = (details.get("description") or "").lower()

                is_strike = (
                    code in ["S", "C", "F", "T", "W", "M"]
                    or "strike" in desc
                    or "foul" in desc
                )
                is_called_or_swinging = ("called strike" in desc) or ("swinging strike" in desc)
                is_swing = any(x in desc for x in ["swing", "foul", "in play"])
                is_whiff = any(x in desc for x in ["swinging strike", "missed bunt"])

                if is_strike:
                    strikes += 1
                    pitch_types[pitch_type]["strikes"] += 1
                if is_called_or_swinging:
                    called_or_swinging += 1
                if is_swing:
                    swings += 1
                    pitch_types[pitch_type]["swings"] += 1
                if is_whiff:
                    whiffs += 1
                    pitch_types[pitch_type]["whiffs"] += 1

                if play_pitch_number == 1:
                    first_pitch_total += 1
                    if is_strike:
                        first_pitch_strikes += 1

                coords = pitch_data.get("coordinates", {})
                px = safe_float(coords.get("pX"), None)
                pz = safe_float(coords.get("pZ"), None)
                sz_top = safe_float(pitch_data.get("strikeZoneTop"), None)
                sz_bot = safe_float(pitch_data.get("strikeZoneBottom"), None)

                if px is not None and pz is not None and sz_top is not None and sz_bot is not None:
                    if -0.83 <= px <= 0.83 and sz_bot <= pz <= sz_top:
                        zone_pitches += 1

                velo = pitch_data.get("startSpeed")
                if velo:
                    v = float(velo)
                    all_velos.append(v)
                    pitch_types[pitch_type]["velos"].append(v)

                breaks = pitch_data.get("breaks", {})
                spin = breaks.get("spinRate")
                if spin:
                    s = float(spin)
                    all_spins.append(s)
                    pitch_types[pitch_type]["spins"].append(s)

                break_length = breaks.get("breakLength")
                if break_length:
                    b = float(break_length)
                    all_breaks.append(b)
                    pitch_types[pitch_type]["breaks"].append(b)

                rx = safe_float(coords.get("x"), None)
                ry = safe_float(coords.get("y"), None)
                if rx is not None and ry is not None:
                    release_points.append((rx, ry))

            ev = hit_data.get("launchSpeed")
            la = hit_data.get("launchAngle")
            if ev:
                ev = float(ev)
                exit_velos.append(ev)
                summary["balls_in_play"] += 1
                pitch_types[pitch_type]["bip"] += 1

                if ev >= 95:
                    summary["hard_hit"] += 1
                    pitch_types[pitch_type]["hard_hit"] += 1

                if la is not None:
                    la = float(la)
                    if ev >= 98 and 20 <= la <= 35:
                        summary["barrels"] += 1
                        pitch_types[pitch_type]["barrels"] += 1

    summary["total_pitches"] = total_pitches
    summary["strike_pct"] = round((strikes / total_pitches) * 100, 1) if total_pitches else 0
    summary["whiff_pct"] = round((whiffs / swings) * 100, 1) if swings else 0
    summary["zone_pct"] = round((zone_pitches / total_pitches) * 100, 1) if total_pitches else 0
    summary["first_pitch_strike_pct"] = round((first_pitch_strikes / first_pitch_total) * 100, 1) if first_pitch_total else 0
    summary["csw_pct"] = round((called_or_swinging / total_pitches) * 100, 1) if total_pitches else 0

    summary["avg_ev"] = avg(exit_velos)
    summary["max_ev"] = round(max(exit_velos), 1) if exit_velos else 0
    summary["early_avg_ev"] = avg(exit_velos[:4])
    summary["recent_avg_ev"] = avg(exit_velos[-4:])
    summary["ev_trend"] = round(summary["recent_avg_ev"] - summary["early_avg_ev"], 1) if len(exit_velos) >= 6 else 0

    early_velo = avg(all_velos[:10])
    recent_velo = avg(all_velos[-10:])
    summary["velo_drop"] = round(max(0, early_velo - recent_velo), 1) if early_velo and recent_velo else 0

    early_spin = avg(all_spins[:10])
    recent_spin = avg(all_spins[-10:])
    summary["spin_drop"] = round(max(0, early_spin - recent_spin), 1) if early_spin and recent_spin else 0

    early_break = avg(all_breaks[:10])
    recent_break = avg(all_breaks[-10:])
    summary["movement_drop"] = round(max(0, early_break - recent_break), 1) if early_break and recent_break else 0

    if len(release_points) >= 10:
        early = release_points[:5]
        recent = release_points[-5:]
        ex, ey = avg([p[0] for p in early]), avg([p[1] for p in early])
        rx, ry = avg([p[0] for p in recent]), avg([p[1] for p in recent])
        summary["release_drift"] = round(math.sqrt((rx - ex) ** 2 + (ry - ey) ** 2), 2)

    cleaned = {}
    for ptype, data in pitch_types.items():
        velos = data["velos"]
        spins = data["spins"]
        breaks = data["breaks"]

        early_v = avg(velos[:5])
        recent_v = avg(velos[-5:])
        early_s = avg(spins[:5])
        recent_s = avg(spins[-5:])
        early_b = avg(breaks[:5])
        recent_b = avg(breaks[-5:])

        cleaned[ptype] = {
            "count": data["count"],
            "avg_velo": avg(velos),
            "recent_velo": recent_v,
            "velo_drop": round(max(0, early_v - recent_v), 1) if early_v and recent_v else 0,
            "avg_spin": avg(spins),
            "recent_spin": recent_s,
            "spin_drop": round(max(0, early_s - recent_s), 1) if early_s and recent_s else 0,
            "avg_break": avg(breaks),
            "recent_break": recent_b,
            "movement_drop": round(max(0, early_b - recent_b), 1) if early_b and recent_b else 0,
            "hard_hit": data["hard_hit"],
            "barrels": data["barrels"],
            "bip": data["bip"],
            "whiff_pct": round((data["whiffs"] / data["swings"]) * 100, 1) if data["swings"] else 0,
            "strike_pct": round((data["strikes"] / data["count"]) * 100, 1) if data["count"] else 0,
        }

    summary["pitch_types"] = cleaned
    return summary


def pitch_type_red_flags(q):
    flags = []
    for ptype, d in q["pitch_types"].items():
        if d["count"] < 5:
            continue
        if d["velo_drop"] >= 1.5:
            flags.append(f"{ptype}: velo down {d['velo_drop']} mph")
        if d["spin_drop"] >= 150:
            flags.append(f"{ptype}: spin down {d['spin_drop']} rpm")
        if d["movement_drop"] >= 2:
            flags.append(f"{ptype}: movement down {d['movement_drop']}")
        if d["hard_hit"] >= 2:
            flags.append(f"{ptype}: {d['hard_hit']} hard-hit balls")
        if d["barrels"] >= 1:
            flags.append(f"{ptype}: {d['barrels']} barrel(s)")
        if d["whiff_pct"] <= 10 and d["count"] >= 8:
            flags.append(f"{ptype}: low whiff {d['whiff_pct']}%")
    return flags[:5]


def pitcher_stress_score(p, q, traffic):
    score = 0
    outs = p.get("outs_recorded", 0)
    pitches = p.get("pitch_count", 0)
    baserunners_total = p.get("hits", 0) + p.get("walks", 0) + p.get("hbp", 0)
    pitches_per_out = round(pitches / outs, 2) if outs else pitches
    baserunners_per_inning = round(baserunners_total / p["innings"], 2) if p.get("innings", 0) else baserunners_total

    if pitches >= 95:
        score += 22
    elif pitches >= 85:
        score += 17
    elif pitches >= 75:
        score += 12
    elif pitches >= 60:
        score += 7

    if pitches_per_out >= 6.5:
        score += 22
    elif pitches_per_out >= 5.5:
        score += 16
    elif pitches_per_out >= 4.7:
        score += 9

    if baserunners_per_inning >= 2.0:
        score += 20
    elif baserunners_per_inning >= 1.5:
        score += 14
    elif baserunners_per_inning >= 1.1:
        score += 8

    if traffic["recent_baserunners"] >= 5:
        score += 18
    elif traffic["recent_baserunners"] >= 3:
        score += 11

    if traffic["consecutive_baserunners"] >= 3:
        score += 15
    elif traffic["consecutive_baserunners"] >= 2:
        score += 8

    if q["velo_drop"] >= 2:
        score += 12
    elif q["velo_drop"] >= 1:
        score += 7

    if q["spin_drop"] >= 250:
        score += 8
    elif q["spin_drop"] >= 150:
        score += 5

    if q["release_drift"] >= 2:
        score += 6

    return round(clamp(score))


def pitcher_dominance_score(p, q, traffic):
    score = 0
    outs = p.get("outs_recorded", 0)
    pitches = p.get("pitch_count", 0)
    baserunners_total = p.get("hits", 0) + p.get("walks", 0) + p.get("hbp", 0)
    pitches_per_out = round(pitches / outs, 2) if outs else 99

    if q["strike_pct"] >= 68:
        score += 18
    elif q["strike_pct"] >= 63:
        score += 10

    if q["whiff_pct"] >= 32:
        score += 18
    elif q["whiff_pct"] >= 25:
        score += 10

    if q["csw_pct"] >= 32:
        score += 10

    if q["first_pitch_strike_pct"] >= 65:
        score += 8

    if pitches_per_out <= 4.0 and outs >= 9:
        score += 18
    elif pitches_per_out <= 4.8 and outs >= 9:
        score += 10

    if baserunners_total <= 2 and outs >= 9:
        score += 15
    elif baserunners_total <= 4 and outs >= 12:
        score += 8

    if p["walks"] == 0:
        score += 8
    elif p["walks"] == 1:
        score += 4

    if q["hard_hit"] <= 1 and q["balls_in_play"] >= 5:
        score += 10
    elif q["hard_hit"] <= 2 and q["balls_in_play"] >= 8:
        score += 5

    if traffic["recent_baserunners"] >= 3:
        score -= 15
    if traffic["consecutive_baserunners"] >= 2:
        score -= 10

    return round(clamp(score))


def contact_quality_score(q, traffic):
    score = 0
    if q["hard_hit"] >= 6:
        score += 30
    elif q["hard_hit"] >= 4:
        score += 22
    elif q["hard_hit"] >= 2:
        score += 10

    if q["barrels"] >= 2:
        score += 22
    elif q["barrels"] >= 1:
        score += 12

    if q["avg_ev"] >= 91:
        score += 12
    elif q["avg_ev"] >= 88:
        score += 7

    if q["max_ev"] >= 108:
        score += 10
    elif q["max_ev"] >= 103:
        score += 6

    if traffic["recent_hits"] >= 4:
        score += 12
    elif traffic["recent_hits"] >= 2:
        score += 6

    return round(clamp(score))


def bullpen_risk_score(info, p):
    inning = safe_int(info["inning"], 1)
    pitches = p.get("pitch_count", 0)
    score = 0

    if inning >= 5 and pitches >= 80:
        score += 25
    elif inning >= 5 and pitches >= 70:
        score += 18
    elif inning >= 4 and pitches >= 80:
        score += 15

    if pitches >= 95:
        score += 18
    elif pitches >= 85:
        score += 12

    if inning >= 6:
        score += 15
    elif inning >= 5:
        score += 10

    if p.get("innings", 0) < 5 and inning >= 5:
        score += 15

    return round(clamp(score))


def remaining_opportunity_score(info, lineup_pressure, bullpen_risk):
    innings_left = innings_remaining_estimate(info)
    score = 0

    if innings_left >= 6:
        score += 35
    elif innings_left >= 4:
        score += 28
    elif innings_left >= 2.5:
        score += 20
    elif innings_left >= 1:
        score += 10
    else:
        score += 2

    score += lineup_pressure * 0.25
    score += bullpen_risk * 0.25

    run_diff = abs(info["home_runs"] - info["away_runs"])
    if info["inning"] >= 8 and run_diff <= 1:
        score += 8

    return round(clamp(score))


def current_inning_pressure_score(info, lineup_pressure, pitcher_stress, contact_quality):
    score = (
        info["base_out_pressure"] * 0.45
        + lineup_pressure * 0.25
        + pitcher_stress * 0.18
        + contact_quality * 0.12
    )

    if info["runners_on"] >= 2 and info["outs"] <= 1:
        score += 8
    if info["base_state"]["third"] and info["outs"] <= 1:
        score += 8
    if info["base_state"]["runners_on"] == 3:
        score += 10

    return round(clamp(score))


def run_suppression_score(info, p, q, traffic, contact_quality):
    total_runs = info["total_runs"]
    inning = info["inning"]
    score = 0

    if inning >= 4 and total_runs <= 2:
        score += 25
    elif inning >= 5 and total_runs <= 4:
        score += 15

    score += contact_quality * 0.25

    if traffic["recent_baserunners"] >= 4:
        score += 18
    elif traffic["recent_baserunners"] >= 2:
        score += 9

    if p["pitch_count"] >= 75:
        score += 10

    if q["hard_hit"] >= 4:
        score += 10

    return round(clamp(score))


def false_dominance_score(dominance, stress, contact_quality, traffic):
    score = 0
    if dominance >= 55 and stress >= 55:
        score += 35
    if contact_quality >= 55:
        score += 20
    if traffic["recent_baserunners"] >= 3:
        score += 20
    if traffic["consecutive_baserunners"] >= 2:
        score += 15
    return round(clamp(score))


def contact_trend_score(q):
    """
    Measures whether contact quality is improving or dying.
    Positive trend helps OVER. Negative trend helps UNDER.
    """
    score = 50
    trend = q.get("ev_trend", 0)

    if trend >= 8:
        score += 28
    elif trend >= 5:
        score += 20
    elif trend >= 3:
        score += 12
    elif trend <= -8:
        score -= 28
    elif trend <= -5:
        score -= 20
    elif trend <= -3:
        score -= 12

    if q.get("hard_hit", 0) >= 4:
        score += 10
    if q.get("barrels", 0) >= 1:
        score += 8
    if q.get("balls_in_play", 0) >= 6 and q.get("hard_hit", 0) <= 1:
        score -= 12

    return round(clamp(score))


def times_through_order_score(info, p, hitters):
    """
    Estimates third-time-through-the-order danger.
    This is a forward-looking OVER signal, not just current pressure.
    """
    bf = p.get("batters_faced", 0)
    inning = info.get("inning", 1)
    score = 0

    if bf >= 27:
        score += 35
    elif bf >= 23:
        score += 28
    elif bf >= 18:
        score += 18
    elif bf >= 14:
        score += 8

    if inning >= 5 and bf >= 18:
        score += 12

    if hitters:
        top_slots = sum(1 for h in hitters[:3] if h.get("slot") in [1, 2, 3, 4, 5])
        score += top_slots * 5

    return round(clamp(score))


def starter_exit_probability(info, p, stress, bullpen):
    """
    Predicts starter-to-bullpen transition risk.
    This is one of the most important live-total transition moments.
    """
    pitches = p.get("pitch_count", 0)
    inning = info.get("inning", 1)
    prob = 0

    if pitches >= 100:
        prob += 70
    elif pitches >= 90:
        prob += 58
    elif pitches >= 80:
        prob += 45
    elif pitches >= 70:
        prob += 28
    elif pitches >= 60:
        prob += 15

    if inning >= 6:
        prob += 18
    elif inning >= 5:
        prob += 10

    prob += stress * 0.15
    prob += bullpen * 0.10

    return round(clamp(prob))


def fake_pressure_score(info, p, q, traffic, dominance, contact, current_pressure):
    """
    Reduces false OVER alerts when traffic exists but the quality underneath is weak.
    """
    score = 0

    if current_pressure >= 55 and contact <= 35:
        score += 25
    if traffic.get("recent_baserunners", 0) >= 2 and q.get("avg_ev", 0) and q.get("avg_ev", 0) < 86:
        score += 20
    if dominance >= 60 and contact <= 40:
        score += 20
    if q.get("strike_pct", 0) >= 66 and q.get("hard_hit", 0) <= 1 and q.get("balls_in_play", 0) >= 5:
        score += 18
    if info.get("runners_on", 0) >= 1 and q.get("whiff_pct", 0) >= 28:
        score += 10

    return round(clamp(score))


def market_resistance_score(opening, live, info, current_pressure, contact, dominance):
    """
    Detects whether the market is resisting the scoreboard.
    Positive = OVER help when live total is suppressed despite real pressure.
    Negative = UNDER help when live total is inflated without real pressure.
    """
    mp = market_pressure(opening, live)
    score = 0

    if mp["direction"] == "suppressed":
        score += 18
        if current_pressure >= 55 or contact >= 50:
            score += 18
        if dominance >= 65 and contact <= 35:
            score -= 22

    if mp["direction"] == "inflated":
        score -= 18
        if dominance >= 55 and current_pressure <= 35 and contact <= 40:
            score -= 22
        if contact >= 60 or current_pressure >= 65:
            score += 12

    return round(max(-100, min(100, score)))


def blowout_kill_score(info):
    """
    Late blowouts reduce scoring intent and can kill overs.
    """
    run_diff = abs(info.get("home_runs", 0) - info.get("away_runs", 0))
    inning = info.get("inning", 1)
    score = 0

    if inning >= 7 and run_diff >= 6:
        score += 45
    elif inning >= 6 and run_diff >= 5:
        score += 30
    elif inning >= 5 and run_diff >= 7:
        score += 25

    return round(clamp(score))



def pitcher_team_side(feed, pitcher_id):
    """Return 'home' or 'away' for the current pitcher using the live boxscore."""
    if not pitcher_id:
        return None
    pid = f"ID{pitcher_id}"
    box = feed.get("liveData", {}).get("boxscore", {})
    for side in ["home", "away"]:
        players = box.get("teams", {}).get(side, {}).get("players", {})
        if pid in players:
            return side
    return None


def team_pitching_summary(feed, side):
    """
    Current-game pitching summary for one team. Uses the MLB live feed only.
    This is not a season bullpen rating; it tells us whether the current game is
    becoming a strikeout/lockdown environment.
    """
    empty = {
        "pitchers_used": 0,
        "bullpen_pitchers_used": 0,
        "outs": 0,
        "bullpen_outs": 0,
        "runs": 0,
        "hits": 0,
        "walks": 0,
        "strikeouts": 0,
        "bullpen_runs": 0,
        "bullpen_hits": 0,
        "bullpen_walks": 0,
        "bullpen_strikeouts": 0,
    }
    if side not in ["home", "away"]:
        return empty

    team = feed.get("liveData", {}).get("boxscore", {}).get("teams", {}).get(side, {})
    players = team.get("players", {}) or {}
    pitcher_ids = team.get("pitchers", []) or []
    starter_id = pitcher_ids[0] if pitcher_ids else None

    out = dict(empty)
    for raw_id in pitcher_ids:
        pdata = players.get(f"ID{raw_id}", {})
        stats = pdata.get("stats", {}).get("pitching", {}) or {}
        if not stats:
            continue
        innings_raw = str(stats.get("inningsPitched", "0"))
        outs = int(math.floor(safe_float(innings_raw.replace(".1", ".33").replace(".2", ".67"), 0))) * 3
        if ".1" in innings_raw:
            outs += 1
        elif ".2" in innings_raw:
            outs += 2

        runs = safe_int(stats.get("runs"), 0)
        hits = safe_int(stats.get("hits"), 0)
        walks = safe_int(stats.get("baseOnBalls"), 0)
        strikeouts = safe_int(stats.get("strikeOuts"), 0)

        out["pitchers_used"] += 1
        out["outs"] += outs
        out["runs"] += runs
        out["hits"] += hits
        out["walks"] += walks
        out["strikeouts"] += strikeouts

        if raw_id != starter_id:
            out["bullpen_pitchers_used"] += 1
            out["bullpen_outs"] += outs
            out["bullpen_runs"] += runs
            out["bullpen_hits"] += hits
            out["bullpen_walks"] += walks
            out["bullpen_strikeouts"] += strikeouts

    return out


def game_pitching_context(feed, info):
    """Combined current-game pitching context from both boxscores."""
    home = team_pitching_summary(feed, "home")
    away = team_pitching_summary(feed, "away")
    cur_side = pitcher_team_side(feed, info.get("pitcher_id"))
    cur_team = team_pitching_summary(feed, cur_side) if cur_side else {}
    return {"home": home, "away": away, "current_pitcher_team": cur_team, "current_pitcher_side": cur_side}


def strikeout_environment_score(info, p, q, traffic, pitching_context=None):
    """
    Measures whether the game is suppressing runs through strikeouts/whiffs.
    High score supports UNDER and warns against weak OVERs.
    """
    score = 0
    outs_recorded = max(1, safe_int(p.get("outs_recorded"), 0))
    k = safe_int(p.get("strikeouts"), 0)
    k_per_out = k / outs_recorded

    if k_per_out >= 0.55 and outs_recorded >= 6:
        score += 26
    elif k_per_out >= 0.42 and outs_recorded >= 6:
        score += 18
    elif k_per_out >= 0.30:
        score += 10

    if q.get("whiff_pct", 0) >= 34:
        score += 18
    elif q.get("whiff_pct", 0) >= 28:
        score += 10

    if q.get("csw_pct", 0) >= 31:
        score += 14
    elif q.get("csw_pct", 0) >= 28:
        score += 8

    if traffic.get("recent_strikeouts", 0) >= 3:
        score += 12
    elif traffic.get("recent_strikeouts", 0) >= 2:
        score += 7

    ctx = pitching_context or {}
    total_k = 0
    total_outs = 0
    for side in ["home", "away"]:
        node = ctx.get(side, {}) or {}
        total_k += safe_int(node.get("strikeouts"), 0)
        total_outs += safe_int(node.get("outs"), 0)

    if total_outs >= 18:
        game_k_rate = total_k / max(1, total_outs)
        if game_k_rate >= 0.42:
            score += 20
        elif game_k_rate >= 0.32:
            score += 12

    return round(clamp(score))


def bullpen_lockdown_score(info, p, q, pitching_context=None):
    """
    Measures whether late-inning run suppression is likely from the bullpen profile
    that is actually appearing in the game. High score supports UNDER and pushes
    against late OVERs.
    """
    ctx = pitching_context or {}
    cur = ctx.get("current_pitcher_team", {}) or {}
    inning = safe_int(info.get("inning", 1), 1)
    score = 0

    bp_outs = safe_int(cur.get("bullpen_outs"), 0)
    bp_k = safe_int(cur.get("bullpen_strikeouts"), 0)
    bp_hits = safe_int(cur.get("bullpen_hits"), 0)
    bp_walks = safe_int(cur.get("bullpen_walks"), 0)
    bp_runs = safe_int(cur.get("bullpen_runs"), 0)

    if bp_outs >= 3:
        bp_k_rate = bp_k / max(1, bp_outs)
        traffic_allowed = bp_hits + bp_walks
        if bp_k_rate >= 0.45:
            score += 25
        elif bp_k_rate >= 0.30:
            score += 15
        if traffic_allowed == 0:
            score += 18
        elif traffic_allowed <= 1:
            score += 10
        if bp_runs == 0:
            score += 12
        else:
            score -= min(20, bp_runs * 10)
    else:
        # Pre-bullpen estimate: a dominant starter + later inning means the next arms
        # may only need a short bridge. This is a modest score, not a hard assumption.
        if inning >= 6 and p.get("pitch_count", 0) <= 85 and q.get("whiff_pct", 0) >= 28:
            score += 15
        elif inning >= 6:
            score += 6

    if inning >= 7:
        score += 8
    if q.get("whiff_pct", 0) >= 34 and q.get("max_ev", 120) < 95:
        score += 10
    if q.get("avg_ev", 100) and q.get("avg_ev", 100) < 86 and q.get("balls_in_play", 0) >= 4:
        score += 8

    return round(clamp(score))


def traffic_conversion_score(info, p, q, traffic):
    """
    OVER regression score: baserunners/contact without enough runs yet.
    High score says pressure may convert soon. Low score does not automatically mean UNDER.
    """
    baserunners = safe_int(p.get("hits"), 0) + safe_int(p.get("walks"), 0) + safe_int(p.get("hbp"), 0)
    runs = safe_int(p.get("runs"), 0)
    score = 0

    stranded_like = baserunners - runs
    if baserunners >= 7 and stranded_like >= 5:
        score += 30
    elif baserunners >= 5 and stranded_like >= 3:
        score += 22
    elif baserunners >= 3 and stranded_like >= 2:
        score += 12

    if traffic.get("recent_baserunners", 0) >= 4:
        score += 18
    elif traffic.get("recent_baserunners", 0) >= 3:
        score += 10

    if traffic.get("consecutive_baserunners", 0) >= 3:
        score += 16
    elif traffic.get("consecutive_baserunners", 0) >= 2:
        score += 8

    if q.get("barrels", 0) >= 2 and runs <= 2:
        score += 14
    elif q.get("barrels", 0) >= 1 and runs <= 1:
        score += 8

    # Strikeout-heavy environments suppress traffic conversion.
    if q.get("whiff_pct", 0) >= 34 and traffic.get("recent_strikeouts", 0) >= 2:
        score -= 14

    return round(clamp(score))


def hard_hit_efficiency_score(info, p, q, traffic):
    """
    Measures whether hard contact is actually becoming hits/traffic.
    Positive = OVER pressure from hard contact not fully paid off yet.
    Negative values are not returned; UNDER support is handled by hard_hit_under_support_score.
    """
    hard = safe_int(q.get("hard_hit"), 0)
    bip = safe_int(q.get("balls_in_play"), 0)
    hits = safe_int(p.get("hits"), 0)
    barrels = safe_int(q.get("barrels"), 0)
    score = 0

    if bip >= 6 and hard >= 4 and hits <= 2:
        score += 28
    elif bip >= 5 and hard >= 3 and hits <= 2:
        score += 18
    elif hard >= 2 and hits <= 1:
        score += 10

    if barrels >= 2 and hits <= 3:
        score += 18
    elif barrels >= 1 and hits <= 2:
        score += 8

    if q.get("avg_ev", 0) >= 91 and hits <= 3:
        score += 10
    if q.get("ev_trend", 0) >= 5:
        score += 8

    # If strikeouts are killing traffic, reduce the regression pressure.
    if q.get("whiff_pct", 0) >= 34:
        score -= 8

    return round(clamp(score))


def hard_hit_under_support_score(info, p, q, traffic):
    """
    UNDER support from weak or inefficient contact plus limited baserunners.
    This is the lesson from Mets/Mariners: hard-hit rate alone is not enough.
    """
    hits = safe_int(p.get("hits"), 0)
    walks = safe_int(p.get("walks"), 0)
    bip = safe_int(q.get("balls_in_play"), 0)
    hard = safe_int(q.get("hard_hit"), 0)
    score = 0

    if hits + walks <= 2 and bip >= 6:
        score += 18
    elif hits + walks <= 3:
        score += 10

    if q.get("avg_ev", 100) < 86 and bip >= 5:
        score += 18
    elif q.get("avg_ev", 100) < 88 and bip >= 4:
        score += 10

    if hard <= 1 and bip >= 5:
        score += 16
    elif hard <= 2 and bip >= 6:
        score += 8

    if q.get("whiff_pct", 0) >= 30 and hits <= 3:
        score += 12

    return round(clamp(score))


def extreme_total_risk(side, line, edge, scores):
    """
    Prevents low-quality strikes at extreme totals like OVER 11.5 / UNDER 12.5
    unless the projection, confirmation, and edge are all elite.
    """
    line = safe_float(line, None)
    if line is None or line < MAX_EXTREME_TOTAL_STRIKE_LINE:
        return False, "normal total"

    projection = safe_int(scores.get("projection_score"), 0)
    confirmation = safe_int(scores.get("confirmation_score"), 0)
    strong_edge = abs(safe_float(edge, 0)) >= MIN_EXTREME_TOTAL_EDGE
    elite_scores = projection >= MIN_EXTREME_TOTAL_PROJECTION and confirmation >= MIN_EXTREME_TOTAL_CONFIRMATION

    if strong_edge and elite_scores:
        return False, "extreme total allowed by elite confirmation"
    return True, "extreme total suppressed"


def under_environment_score(info, p, q, traffic, dominance, contact, current_pressure, remaining_opp, fake_pressure, blowout):
    """
    Builds real UNDER paths instead of only allowing UNDER through raw projected edge.
    """
    score = 0

    if dominance >= 70:
        score += 30
    elif dominance >= 60:
        score += 22
    elif dominance >= 50:
        score += 12

    if contact <= 25 and q.get("balls_in_play", 0) >= 5:
        score += 22
    elif contact <= 35:
        score += 12

    if current_pressure <= 25:
        score += 18
    elif current_pressure <= 35:
        score += 10

    if traffic.get("recent_baserunners", 0) <= 1:
        score += 12

    if remaining_opp <= 35:
        score += 14

    score += fake_pressure * 0.30
    score += blowout * 0.20

    if p.get("walks", 0) == 0 and q.get("strike_pct", 0) >= 64:
        score += 8

    return round(clamp(score))



def run_conversion_score(info, p, q, traffic, hitters, current_pressure, remaining_opp, stress, contact, lineup, contact_trend, tto, starter_exit, fake_pressure, traffic_conversion=0, hard_hit_efficiency=0, strikeout_environment=0, bullpen_lockdown=0):
    """
    OVER quality score: pressure must be likely to become actual runs.
    This prevents the bot from betting every traffic situation.
    """
    score = 0
    outs = info.get("outs", 0)
    runners_on = info.get("runners_on", 0)
    base_state = info.get("base_state", {})

    # Base/out conversion value.
    if base_state.get("third") and outs <= 1:
        score += 26
    elif base_state.get("second") and outs <= 1:
        score += 20
    elif runners_on >= 2 and outs <= 1:
        score += 28
    elif runners_on >= 2 and outs == 2:
        score += 12
    elif runners_on == 1 and outs <= 1:
        score += 10

    # Current pressure matters, but only when it can convert.
    if current_pressure >= 70:
        score += 18
    elif current_pressure >= 60:
        score += 12
    elif current_pressure >= 50:
        score += 6

    # Contact quality: hard contact converts pressure.
    if contact >= 65:
        score += 18
    elif contact >= 55:
        score += 12
    elif contact >= 45:
        score += 6

    if q.get("barrels", 0) >= 1:
        score += 10
    if q.get("hard_hit", 0) >= 3:
        score += 8
    if q.get("ev_trend", 0) >= 5:
        score += 8

    # Command/traffic.
    if p.get("walks", 0) >= 2:
        score += 8
    if q.get("strike_pct", 100) <= 55 and q.get("total_pitches", 0) >= 18:
        score += 9
    if q.get("zone_pct", 100) <= 42 and q.get("total_pitches", 0) >= 18:
        score += 6
    if traffic.get("consecutive_baserunners", 0) >= 2:
        score += 10
    if traffic.get("recent_baserunners", 0) >= 4:
        score += 8

    # Future conversion: lineup, starter exit, third time through.
    if lineup >= 75:
        score += 10
    elif lineup >= 65:
        score += 6

    if starter_exit >= 65:
        score += 10
    elif starter_exit >= 50:
        score += 6

    if tto >= 55:
        score += 8

    if remaining_opp >= 65:
        score += 6

    # Swing-and-miss and fake pressure reduce conversion odds.
    if q.get("whiff_pct", 0) >= 34 and q.get("csw_pct", 0) >= 30:
        score -= 16
    elif q.get("whiff_pct", 0) >= 28 and q.get("csw_pct", 0) >= 29:
        score -= 8

    # V2.4: pressure must be able to become runs.
    score += traffic_conversion * 0.18
    score += hard_hit_efficiency * 0.14
    score -= strikeout_environment * 0.12
    score -= bullpen_lockdown * 0.10

    score -= fake_pressure * 0.22

    # Two outs makes conversion harder unless there is elite contact/command collapse.
    if outs == 2 and current_pressure < 70 and contact < 60:
        score -= 10

    return round(clamp(score))


def run_prevention_score(info, p, q, traffic, dominance, contact, current_pressure, remaining_opp, fake_pressure, under_environment, blowout, strikeout_environment=0, bullpen_lockdown=0, hard_hit_under_support=0, traffic_conversion=0, hard_hit_efficiency=0):
    """
    UNDER quality score: low scoring must be likely to continue.
    """
    score = 0

    if dominance >= 75:
        score += 24
    elif dominance >= 65:
        score += 18
    elif dominance >= 55:
        score += 10

    if q.get("whiff_pct", 0) >= 32 and q.get("csw_pct", 0) >= 30:
        score += 18
    elif q.get("whiff_pct", 0) >= 26 and q.get("csw_pct", 0) >= 28:
        score += 10

    if contact <= 25 and q.get("balls_in_play", 0) >= 4:
        score += 18
    elif contact <= 35:
        score += 10

    if current_pressure <= 25:
        score += 16
    elif current_pressure <= 35:
        score += 8

    if traffic.get("recent_baserunners", 0) <= 1:
        score += 10

    if p.get("walks", 0) == 0 and q.get("strike_pct", 0) >= 64:
        score += 8

    if remaining_opp <= 35:
        score += 10

    score += fake_pressure * 0.20
    score += under_environment * 0.25
    score += blowout * 0.12
    score += strikeout_environment * 0.18
    score += bullpen_lockdown * 0.16
    score += hard_hit_under_support * 0.12
    score -= traffic_conversion * 0.10
    score -= hard_hit_efficiency * 0.08

    # Under danger penalties.
    if current_pressure >= 60:
        score -= 15
    if contact >= 55:
        score -= 15
    if p.get("walks", 0) >= 2:
        score -= 8
    if q.get("barrels", 0) >= 1:
        score -= 8

    return round(clamp(score))


def predictive_market_move_score(opening, live, info, p, q, traffic, scores):
    """
    Attempts to identify the market moving BEFORE runs score.
    This is a WATCH-first engine unless the run conversion/prevention is already strong.
    Positive score = likely upward total move.
    Negative score = likely downward total move.
    """
    if live is None:
        return 0

    move = 0
    if opening is not None:
        move = live - opening

    score = 0

    # Leading OVER indicators before score changes.
    if p.get("batters_faced", 0) >= 6:
        pitches_per_batter = p.get("pitch_count", 0) / max(1, p.get("batters_faced", 1))
        if pitches_per_batter >= 4.2:
            score += 18
        elif pitches_per_batter >= 3.8:
            score += 10

    if q.get("strike_pct", 100) <= 55 and q.get("total_pitches", 0) >= 18:
        score += 14
    if q.get("zone_pct", 100) <= 42 and q.get("total_pitches", 0) >= 18:
        score += 10
    if q.get("ev_trend", 0) >= 5:
        score += 12
    if q.get("hard_hit", 0) >= 2:
        score += 10
    if q.get("barrels", 0) >= 1:
        score += 12
    if traffic.get("recent_baserunners", 0) >= 3:
        score += 14
    if traffic.get("consecutive_baserunners", 0) >= 2:
        score += 12
    if scores.get("times_through_order", 0) >= 45:
        score += 8
    if scores.get("starter_exit_probability", 0) >= 55:
        score += 10
    if scores.get("lineup_pressure", 0) >= 70:
        score += 8

    # If the market has already moved upward, reduce predictive score.
    if move >= 1.0:
        score -= int(move * 18)

    # Leading UNDER indicators.
    under_pull = 0
    if scores.get("run_prevention", 0) >= 70:
        under_pull += 18
    if scores.get("under_environment", 0) >= 60:
        under_pull += 14
    if scores.get("fake_pressure", 0) >= 55:
        under_pull += 12
    if scores.get("current_inning_pressure", 0) <= 25 and scores.get("contact_quality", 0) <= 35:
        under_pull += 10

    if under_pull > score:
        return -round(clamp(under_pull))

    return round(clamp(score))


def best_entry_guidance(side, live, opening, edge, action, scores):
    """
    Gives practical entry guidance so the alert does not mean 'bet at any price.'
    """
    if live is None:
        return "No live line available."

    move = 0
    if opening is not None:
        move = live - opening

    if side == "OVER":
        strong_line = live - BEST_ENTRY_HALF_RUN_BUFFER
        if move >= MARKET_HAS_ALREADY_MOVED_RUNS and action == "WATCH":
            return f"Best entry: {strong_line:.1f} or lower. Current {live} has already moved +{move:.1f}; avoid chasing unless pressure continues."
        if scores.get("confirmation_score", 0) >= 68 or scores.get("run_conversion", 0) >= 70:
            return f"Best entry: {live} playable; {strong_line:.1f} or lower is stronger."
        return f"Best entry: wait for {strong_line:.1f} or renewed pressure confirmation."

    if side == "UNDER":
        strong_line = live + BEST_ENTRY_HALF_RUN_BUFFER
        if move <= -MARKET_HAS_ALREADY_MOVED_RUNS and action == "WATCH":
            return f"Best entry: {strong_line:.1f} or higher. Current {live} has already moved down {move:.1f}; avoid chasing unless run prevention stays strong."
        if scores.get("confirmation_score", 0) >= 68 or scores.get("run_prevention", 0) >= 75:
            return f"Best entry: {live} playable; {strong_line:.1f} or higher is stronger."
        return f"Best entry: wait for {strong_line:.1f} or cleaner run-prevention confirmation."

    return "No entry guidance."


def pressure_to_runs_score(info, p, q, traffic, hitters, scores):
    """
    Measures whether pressure is likely to become ACTUAL RUNS.
    This is different from simple pressure.

    Strong OVER predictors:
      - Runner in scoring position with <2 outs
      - Hard contact / barrel
      - Command problems
      - Top/middle lineup
      - Starter stress and early bullpen risk
    """
    score = 0
    outs = info.get("outs", 0)
    runners = info.get("runners_on", 0)
    base = info.get("base_state", {})

    # Base/out run conversion.
    if base.get("third") and outs <= 1:
        score += 24
    elif base.get("second") and outs <= 1:
        score += 18
    elif runners >= 2 and outs <= 1:
        score += 26
    elif runners >= 2 and outs == 2:
        score += 10
    elif runners == 1 and outs <= 1:
        score += 8

    # Quality of contact.
    if scores.get("contact_quality", 0) >= 60:
        score += 16
    elif scores.get("contact_quality", 0) >= 48:
        score += 10

    if q.get("barrels", 0) >= 1:
        score += 10
    if q.get("hard_hit", 0) >= 2:
        score += 8
    if q.get("ev_trend", 0) >= 5:
        score += 8

    # Pitcher command and stress.
    if scores.get("pitcher_stress", 0) >= 60:
        score += 12
    elif scores.get("pitcher_stress", 0) >= 45:
        score += 8

    if q.get("strike_pct", 100) <= 56 and q.get("total_pitches", 0) >= 18:
        score += 9
    if q.get("zone_pct", 100) <= 42 and q.get("total_pitches", 0) >= 18:
        score += 7
    if p.get("walks", 0) >= 2:
        score += 8

    # Traffic sequencing.
    if traffic.get("recent_baserunners", 0) >= 4:
        score += 10
    elif traffic.get("recent_baserunners", 0) >= 2:
        score += 6
    if traffic.get("consecutive_baserunners", 0) >= 2:
        score += 8

    # Lineup and future path.
    if scores.get("lineup_pressure", 0) >= 75:
        score += 10
    elif scores.get("lineup_pressure", 0) >= 62:
        score += 6

    if scores.get("starter_exit_probability", 0) >= 60:
        score += 8
    if scores.get("times_through_order", 0) >= 45:
        score += 6

    # Penalize fake pressure and dominant swing-and-miss.
    score -= scores.get("fake_pressure", 0) * 0.22

    if q.get("whiff_pct", 0) >= 34 and q.get("csw_pct", 0) >= 31 and scores.get("contact_quality", 0) < 50:
        score -= 12
    elif q.get("whiff_pct", 0) >= 28 and q.get("csw_pct", 0) >= 29 and scores.get("contact_quality", 0) < 45:
        score -= 7

    if outs == 2 and runners < 2 and scores.get("contact_quality", 0) < 55:
        score -= 8

    return round(clamp(score))


def pre_run_over_watch_score(opening, live, info, p, q, traffic, scores):
    """
    Predictive OVER engine.

    Goal:
      Detect 'market likely to move up' BEFORE the runs fully arrive.

    This sends WATCH first, unless the normal STRIKE engine already confirms.
    """
    if not ENABLE_PRE_RUN_OVER_WATCH or live is None:
        return 0

    move = 0
    if opening is not None:
        move = live - opening

    # If the market already moved up too much, this is no longer pre-run value.
    if move > MARKET_LAG_MAX_UPWARD_MOVE:
        return 0

    score = 0

    # Pressure-to-runs is the anchor.
    score += scores.get("pressure_to_runs", 0) * 0.45

    # Leading indicators.
    if p.get("batters_faced", 0) >= 6:
        ppb = p.get("pitch_count", 0) / max(1, p.get("batters_faced", 1))
        if ppb >= 4.2:
            score += 14
        elif ppb >= 3.8:
            score += 8

    if scores.get("pitcher_stress", 0) >= 50:
        score += 10
    if scores.get("contact_quality", 0) >= 45:
        score += 8
    if scores.get("lineup_pressure", 0) >= 65:
        score += 8
    if scores.get("remaining_opportunity", 0) >= 55:
        score += 5

    if q.get("strike_pct", 100) <= 56 and q.get("total_pitches", 0) >= 18:
        score += 8
    if q.get("zone_pct", 100) <= 42 and q.get("total_pitches", 0) >= 18:
        score += 6
    if q.get("hard_hit", 0) >= 2:
        score += 8
    if q.get("barrels", 0) >= 1:
        score += 10
    if traffic.get("recent_baserunners", 0) >= 3:
        score += 9
    if traffic.get("consecutive_baserunners", 0) >= 2:
        score += 8

    # Good over spots are often early or middle game.
    if info.get("inning", 1) <= 4:
        score += 7
    elif info.get("inning", 1) <= 6:
        score += 3

    # Fake pressure and strong run prevention block it.
    if scores.get("fake_pressure", 0) >= PRE_RUN_MAX_FAKE_PRESSURE:
        score -= 18
    if scores.get("under_environment", 0) >= 65:
        score -= 12
    if scores.get("run_prevention", 0) >= 70:
        score -= 12

    return round(clamp(score))


def over_value_score(opening, live, edge, scores):
    """
    Price/number discipline for OVERs.
    We want to predict the move, not chase it.
    """
    if live is None:
        return 0

    move = 0
    if opening is not None:
        move = live - opening

    score = 50
    score += max(0, edge) * 8
    score += scores.get("pressure_to_runs", 0) * 0.20
    score += scores.get("pre_run_over_watch", 0) * 0.15

    if move <= 0.5:
        score += 12
    elif move <= 1.0:
        score += 6
    elif move >= 1.5:
        score -= 14
    elif move >= 2.0:
        score -= 22

    return round(clamp(score))


def threat_index_score(info):
    """
    Immediate run-danger score from base/out state.
    This came directly from the winning alerts: bases and outs mattered as much as raw contact.
    """
    base = info.get("base_state", {})
    outs = safe_int(info.get("outs", 0), 0)
    runners = safe_int(info.get("runners_on", 0), 0)

    first = bool(base.get("first"))
    second = bool(base.get("second"))
    third = bool(base.get("third"))

    if first and second and third:
        raw = 100
    elif first and third:
        raw = 90
    elif second and third:
        raw = 92
    elif first and second:
        raw = 82
    elif third:
        raw = 72
    elif second:
        raw = 65
    elif first:
        raw = 45
    else:
        raw = 10

    if outs == 0:
        raw += 12
    elif outs == 1:
        raw += 4
    elif outs == 2:
        raw -= 18

    if runners >= 2 and outs <= 1:
        raw += 8

    return round(clamp(raw))


def market_lag_score(side, live_total, projected_total):
    """
    Measures how far the live line appears behind the projection.
    Positive score means the market is lagging in the direction of the proposed side.
    """
    if live_total is None or projected_total is None:
        return 0

    gap = projected_total - live_total

    if side == "UNDER":
        gap = -gap

    if gap >= MARKET_LAG_ELITE:
        return 100
    if gap >= MARKET_LAG_STRONG:
        return round(clamp(60 + (gap - MARKET_LAG_STRONG) * 12))
    if gap >= 1.0:
        return round(clamp(35 + gap * 15))
    if gap >= 0.5:
        return 20
    return 0


def signal_stack_score(side, scores):
    """
    Counts aligned winner-pattern signals.
    This is not a hard filter; it helps separate normal strikes from elite strikes.
    """
    stack = 0
    labels = []

    if side == "OVER":
        checks = [
            ("Stress", scores.get("pitcher_stress", 0) >= 65),
            ("Contact", scores.get("contact_quality", 0) >= 55),
            ("P2R", scores.get("pressure_to_runs", 0) >= 70),
            ("Conv", scores.get("run_conversion", 0) >= 65),
            ("Threat", scores.get("threat_index", 0) >= 70),
            ("MarketLag", scores.get("market_lag", 0) >= 60),
            ("PreOver", scores.get("pre_run_over_watch", 0) >= 72),
            ("PredMove", scores.get("predictive_market_move", 0) >= 65),
            ("TrafficConv", scores.get("traffic_conversion", 0) >= 60),
            ("HHEff", scores.get("hard_hit_efficiency", 0) >= 55),
        ]
    else:
        checks = [
            ("Prev", scores.get("run_prevention", 0) >= 70),
            ("UnderEnv", scores.get("under_environment", 0) >= 60),
            ("LowCIP", scores.get("current_inning_pressure", 0) <= 30),
            ("LowContact", scores.get("contact_quality", 0) <= 35),
            ("MarketLag", scores.get("market_lag", 0) >= 60),
            ("NoP2R", scores.get("pressure_to_runs", 0) <= 25),
            ("Dominance", scores.get("pitcher_dominance", 0) >= 55),
            ("KEnv", scores.get("strikeout_environment", 0) >= 60),
            ("BullpenLock", scores.get("bullpen_lockdown", 0) >= 55),
            ("HHUnder", scores.get("hard_hit_under_support", 0) >= 55),
        ]

    for label, ok in checks:
        if ok:
            stack += 1
            labels.append(label)

    return stack, labels[:6]


def p2r_multiplier_boost(scores):
    """
    Additive boost from winner review: P2R was carrying many of the best overs.
    """
    p2r = scores.get("pressure_to_runs", 0)
    if p2r >= P2R_SUPER_ELITE_THRESHOLD:
        return P2R_SUPER_ELITE_BOOST
    if p2r >= P2R_ELITE_THRESHOLD:
        return P2R_ELITE_BOOST
    return 0


def conv_acceleration_score(state, info, side, current_conv):
    """
    Tracks conversion acceleration by game/side using local state only.
    No extra API calls. Rapid Conv jumps are a 'runs are coming' signal.
    """
    if not ENABLE_WINNER_PATTERN_ENHANCEMENTS:
        return 0

    key = f"{today()}::{info.get('away','')}@{info.get('home','')}::{side}::conv"
    games = state.setdefault("games", {})
    node = games.setdefault(key, {})

    prev = safe_int(node.get("last_conv"), current_conv)
    jump = current_conv - prev

    node["last_conv"] = current_conv
    node["last_seen"] = now_local().isoformat()

    if jump >= CONV_ACCEL_MIN_JUMP * 2:
        return 100
    if jump >= CONV_ACCEL_MIN_JUMP:
        return round(clamp(55 + jump))
    if current_conv >= 85:
        return 70
    if current_conv >= 70:
        return 45
    return 0


def apply_winner_pattern_enhancements(state, info, side, scores, live_total=None, projected_total=None):
    """
    Adds the five V2.3.1 winner-pattern signals.
    Additive only. Does not suppress the original engine.
    """
    if not ENABLE_WINNER_PATTERN_ENHANCEMENTS:
        return scores

    scores["threat_index"] = threat_index_score(info)
    scores["market_lag"] = market_lag_score(side, live_total, projected_total)
    scores["p2r_boost"] = p2r_multiplier_boost(scores) if side == "OVER" else 0
    scores["conv_acceleration"] = conv_acceleration_score(
        state, info, side, scores.get("run_conversion", 0)
    )

    stack, labels = signal_stack_score(side, scores)
    scores["signal_stack"] = stack
    scores["signal_stack_labels"] = labels

    return scores


def projection_score(side, edge, scores):
    """
    How strongly the model thinks the current market line is wrong.
    This is separate from whether the live game confirms a bet right now.
    """
    ae = abs(edge)
    score = 0

    if ae >= 3.0:
        score += 55
    elif ae >= 2.0:
        score += 42
    elif ae >= 1.5:
        score += 32
    elif ae >= 1.0:
        score += 22
    elif ae >= 0.7:
        score += 12

    if side == "OVER":
        score += scores.get("over_value", 0) * 0.18
        score += max(0, scores.get("predictive_market_move", 0)) * 0.18
        score += scores.get("pre_run_over_watch", 0) * 0.12
        if scores.get("market_reaction_profile") == "DISCOUNTED_OVER":
            score += scores.get("discounted_over_score", 0) * 0.20
        if scores.get("market_reaction_profile") == "CONTINUATION_OVER":
            score += scores.get("continuation_score", 0) * 0.12
        if scores.get("market_reaction_profile") in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"]:
            score -= scores.get("settle_down_score", 0) * 0.14

        # V2.3.1 winner-pattern boosts.
        score += scores.get("market_lag", 0) * 0.10
        score += scores.get("threat_index", 0) * 0.06
        score += scores.get("p2r_boost", 0)
        score += scores.get("conv_acceleration", 0) * 0.06
        if scores.get("signal_stack", 0) >= SIGNAL_STACK_ELITE:
            score += 8
        elif scores.get("signal_stack", 0) >= SIGNAL_STACK_STRONG:
            score += 4
    else:
        score += scores.get("run_prevention", 0) * 0.20
        score += max(0, -scores.get("predictive_market_move", 0)) * 0.15
        score += scores.get("under_environment", 0) * 0.15
        if scores.get("market_reaction_profile") in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"]:
            score += scores.get("settle_down_score", 0) * 0.22
            score += scores.get("false_inflation_score", 0) * 0.10
        if scores.get("market_reaction_profile") == "CONTINUATION_OVER":
            score -= scores.get("continuation_score", 0) * 0.18

        # V2.3.1 under-side intelligence.
        score += scores.get("market_lag", 0) * 0.08
        if scores.get("signal_stack", 0) >= SIGNAL_STACK_ELITE:
            score += 6
        elif scores.get("signal_stack", 0) >= SIGNAL_STACK_STRONG:
            score += 3

    return round(clamp(score))


def confirmation_score(side, info, scores):
    """
    How much the live game state confirms action right now.
    This prevents misleading 100/100 confidence on low-confirmation WATCH spots.
    """
    inning = safe_int(info.get("inning", 1), 1)

    if side == "OVER":
        score = (
            scores.get("current_inning_pressure", 0) * 0.25
            + scores.get("pressure_to_runs", 0) * 0.25
            + scores.get("run_conversion", 0) * 0.20
            + scores.get("contact_quality", 0) * 0.15
            + scores.get("pitcher_stress", 0) * 0.10
            + scores.get("lineup_pressure", 0) * 0.05
            + scores.get("traffic_conversion", 0) * 0.08
            + scores.get("hard_hit_efficiency", 0) * 0.06
            + (scores.get("continuation_score", 0) * 0.08 if scores.get("market_reaction_profile") == "CONTINUATION_OVER" else 0)
            + (scores.get("discounted_over_score", 0) * 0.06 if scores.get("market_reaction_profile") == "DISCOUNTED_OVER" else 0)
            - scores.get("strikeout_environment", 0) * 0.08
            - scores.get("bullpen_lockdown", 0) * 0.07
            - (scores.get("settle_down_score", 0) * 0.10 if scores.get("market_reaction_profile") in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"] else 0)
        )

        # Late overs need stronger current or near-current evidence.
        if inning >= 8 and scores.get("current_inning_pressure", 0) < 45:
            score -= 12
        elif inning >= 7 and scores.get("current_inning_pressure", 0) < 35:
            score -= 7

        if scores.get("pre_run_over_watch", 0) >= 85 and scores.get("pressure_to_runs", 0) >= 70:
            score += 8

        # V2.3.1: winners often had immediate base/out threat and stacked signals.
        if scores.get("threat_index", 0) >= 85 and scores.get("pressure_to_runs", 0) >= 70:
            score += 8
        elif scores.get("threat_index", 0) >= 70 and scores.get("pressure_to_runs", 0) >= 65:
            score += 5

        if scores.get("conv_acceleration", 0) >= 70:
            score += 6

        if scores.get("signal_stack", 0) >= SIGNAL_STACK_ELITE:
            score += 7
        elif scores.get("signal_stack", 0) >= SIGNAL_STACK_STRONG:
            score += 4

        if scores.get("run_prevention", 0) >= 65:
            score -= 12

    else:
        score = (
            scores.get("run_prevention", 0) * 0.35
            + scores.get("under_environment", 0) * 0.25
            + max(0, 100 - scores.get("current_inning_pressure", 0)) * 0.15
            + max(0, 100 - scores.get("contact_quality", 0)) * 0.10
            + max(0, 100 - scores.get("pressure_to_runs", 0)) * 0.10
            + max(0, -scores.get("predictive_market_move", 0)) * 0.05
            + scores.get("strikeout_environment", 0) * 0.12
            + scores.get("bullpen_lockdown", 0) * 0.10
            + scores.get("hard_hit_under_support", 0) * 0.08
            + (scores.get("settle_down_score", 0) * 0.18 if scores.get("market_reaction_profile") in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"] else 0)
            + (scores.get("false_inflation_score", 0) * 0.08 if scores.get("market_reaction_profile") == "FALSE_INFLATION_FADE" else 0)
            - scores.get("traffic_conversion", 0) * 0.08
            - scores.get("hard_hit_efficiency", 0) * 0.06
            - (scores.get("continuation_score", 0) * 0.14 if scores.get("market_reaction_profile") == "CONTINUATION_OVER" else 0)
        )

        if inning >= 7:
            score += 6
        if scores.get("signal_stack", 0) >= SIGNAL_STACK_ELITE:
            score += 5
        elif scores.get("signal_stack", 0) >= SIGNAL_STACK_STRONG:
            score += 3
        if scores.get("current_inning_pressure", 0) > 45:
            score -= 12
        if scores.get("contact_quality", 0) > 55:
            score -= 12

    return round(clamp(score))


def inning_adjusted_projection(info, live_total, projected_total):
    """
    Caps unrealistic late-game projections so one hot model signal does not produce
    impossible-looking final totals such as +8 to +10 runs after the 7th.
    """
    if live_total is None or projected_total is None:
        return projected_total

    inning = safe_int(info.get("inning", 1), 1)
    cap = None

    if inning >= 9:
        cap = MAX_PROJECTION_EDGE_INNING_9
    elif inning >= 8:
        cap = MAX_PROJECTION_EDGE_INNING_8
    elif inning >= 7:
        cap = MAX_PROJECTION_EDGE_INNING_7

    if cap is None:
        return projected_total

    if projected_total > live_total + cap:
        return round(live_total + cap, 1)
    if projected_total < live_total - cap:
        return round(live_total - cap, 1)

    return projected_total


def need_runs_context(info, live_total, side):
    """
    Simple late-game context for the text:
    how many runs matter relative to the current live total.
    """
    if live_total is None:
        return "Need Runs: live total unavailable"

    current_runs = safe_float(info.get("total_runs", 0), 0)
    innings_left = innings_remaining_estimate(info)

    if side == "OVER":
        need = max(0, math.floor(live_total - current_runs) + 1)
        return f"Need Runs: {need} more to clear OVER {live_total} | InnLeft: {innings_left:.1f}"
    else:
        cushion = max(0, math.ceil(live_total - current_runs) - 1)
        return f"Need Runs: UNDER {live_total} has {cushion} run cushion | InnLeft: {innings_left:.1f}"


def action_window(side, line, edge, scores, info):
    """
    Practical action guidance.
    """
    inning = safe_int(info.get("inning", 1), 1)
    if line is None:
        return "Action Window: no live line"

    if side == "OVER":
        preferred = line - 0.5
        pass_line = line + (1.0 if inning < 7 else 0.5)
        if scores.get("confirmation_score", 0) >= 70:
            return f"Action Window: play {line}; prefer {preferred:.1f}; pass if {pass_line:.1f}+"
        return f"Action Window: wait for {preferred:.1f} or confirmation; pass if {pass_line:.1f}+"

    preferred = line + 0.5
    pass_line = line - (1.0 if inning < 7 else 0.5)
    if scores.get("confirmation_score", 0) >= 70:
        return f"Action Window: play {line}; prefer {preferred:.1f}; pass if {pass_line:.1f}-"
    return f"Action Window: wait for {preferred:.1f} or confirmation; pass if {pass_line:.1f}-"


def clean_scenario_label(scenario):
    """
    Scenario describes environment only. Action decides WATCH/STRIKE.
    Removes contradictions like 'BET NOW' with 'WATCH ONLY' scenario.
    """
    if not scenario:
        return "Market Evaluation"

    cleaned = scenario
    for bad in [" → WATCH ONLY", " → Watch", " Watch", " WATCH ONLY"]:
        cleaned = cleaned.replace(bad, "")
    cleaned = cleaned.replace("Predictive Market Move → Over", "Predictive Over Build")
    cleaned = cleaned.replace("Predictive Market Move → Under", "Predictive Under Build")
    return cleaned.strip()


def confidence_score(side, edge, scenario, scores, evidence, market_resistance):
    """
    Converts the signal stack into a confidence grade used for WATCH/STRIKE.
    """
    ae = abs(edge)
    confidence = 45

    if ae >= 2.0:
        confidence += 22
    elif ae >= 1.5:
        confidence += 16
    elif ae >= 1.0:
        confidence += 10
    elif ae >= 0.7:
        confidence += 5

    confidence += evidence.get("real_signal_count", 0) * 4

    if side == "OVER":
        confidence += scores.get("contact_quality", 0) * 0.08
        confidence += scores.get("pitcher_stress", 0) * 0.07
        confidence += scores.get("lineup_pressure", 0) * 0.06
        confidence += scores.get("starter_exit_probability", 0) * 0.06
        confidence += scores.get("contact_trend", 50) * 0.04
        confidence += scores.get("run_conversion", 0) * 0.14
        confidence += scores.get("pressure_to_runs", 0) * 0.12
        confidence += scores.get("pre_run_over_watch", 0) * 0.10
        confidence += scores.get("over_value", 0) * 0.05
        confidence += scores.get("traffic_conversion", 0) * 0.08
        confidence += scores.get("hard_hit_efficiency", 0) * 0.06
        confidence += max(0, scores.get("predictive_market_move", 0)) * 0.10
        confidence += max(0, market_resistance) * 0.12
        confidence -= scores.get("fake_pressure", 0) * 0.16
        confidence -= scores.get("run_prevention", 0) * 0.06
        confidence -= scores.get("under_environment", 0) * 0.10
        confidence -= scores.get("strikeout_environment", 0) * 0.07
        confidence -= scores.get("bullpen_lockdown", 0) * 0.06
        confidence -= scores.get("blowout_kill", 0) * 0.12
    else:
        confidence += scores.get("dominance", 0) * 0.09
        confidence += scores.get("under_environment", 0) * 0.12
        confidence += scores.get("run_prevention", 0) * 0.14
        confidence += scores.get("strikeout_environment", 0) * 0.10
        confidence += scores.get("bullpen_lockdown", 0) * 0.09
        confidence += scores.get("hard_hit_under_support", 0) * 0.07
        confidence += scores.get("fake_pressure", 0) * 0.10
        confidence += max(0, -market_resistance) * 0.12
        confidence += max(0, -scores.get("predictive_market_move", 0)) * 0.08
        confidence -= scores.get("current_inning_pressure", 0) * 0.08
        confidence -= scores.get("run_conversion", 0) * 0.06
        confidence -= scores.get("contact_quality", 0) * 0.08
        confidence -= scores.get("pitcher_stress", 0) * 0.06
        confidence -= scores.get("traffic_conversion", 0) * 0.08
        confidence -= scores.get("hard_hit_efficiency", 0) * 0.06

    if "Watch" in scenario:
        confidence -= 4
    if "Strike" in scenario or "Opportunity" in scenario:
        confidence += 3

    return round(clamp(confidence))


def action_from_confidence(side, confidence, edge, scores=None):
    """
    V2.2.4 calibrated action engine.
    Projection finds the opportunity; confirmation decides whether it is actionable now.
    """
    scores = scores or {}
    inning = safe_int(scores.get("inning", 1), 1)
    projection = scores.get("projection_score", confidence)
    confirmation = scores.get("confirmation_score", confidence)

    extreme_blocked, _extreme_reason = extreme_total_risk(side, scores.get("live_total", 0), edge, scores)

    # V3.7.6: profile-driven promotion. The classifier may identify a true
    # market-reaction setup before the older generic action model reaches STRIKE.
    # This promotes strong profile candidates to STRIKE while final gate still
    # handles price, stale lines, risk, playable book, and duplicate protection.
    tmp_opportunity = {"side": side, "edge": edge, "scores": scores, "confidence": confidence}
    profile_promote, profile_promote_reason = profile_promotion_reason({"inning": inning}, tmp_opportunity, {})
    if profile_promote and not extreme_blocked:
        scores["profile_promotion_reason"] = profile_promote_reason
        return "STRIKE"

    if side == "OVER":
        min_edge = MIN_LATE_OVER_STRIKE_EDGE_RUNS if inning >= 7 else MIN_OVER_EDGE_RUNS
        min_confirm = MIN_LATE_OVER_CONFIRMATION_FOR_STRIKE if inning >= 7 else MIN_OVER_CONFIRMATION_FOR_STRIKE

        strike_ready = (
            edge >= min_edge
            and projection >= 65
            and confirmation >= min_confirm
            and scores.get("pressure_to_runs", 0) >= (70 if inning >= 7 else 60)
            and scores.get("run_conversion", 0) >= (62 if inning >= 7 else 55)
        )
        if strike_ready and not extreme_blocked:
            return "STRIKE"

        watch_ready = (
            edge >= MIN_WATCH_EDGE_RUNS
            and projection >= 55
            and (
                confirmation >= 40
                or scores.get("pre_run_over_watch", 0) >= PRE_RUN_OVER_WATCH_SCORE
                or scores.get("predictive_market_move", 0) >= MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH
            )
        )
        return "WATCH" if watch_ready else "NO_PLAY"

    # UNDER
    min_edge = MIN_UNDER_STRIKE_EDGE_RUNS
    min_confirm = MIN_LATE_UNDER_CONFIRMATION_FOR_STRIKE if inning >= 7 else MIN_UNDER_CONFIRMATION_FOR_STRIKE

    inflated_fade_ready = (
        ENABLE_MARKET_REACTION_ENGINE
        and scores.get("market_reaction_profile") in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"]
        and safe_float(scores.get("market_reaction_move"), 0) >= INFLATED_TOTAL_MOVE_RUNS
        and inning >= INFLATED_UNDER_MIN_INNING
        and edge <= -INFLATED_UNDER_MIN_EDGE
        and scores.get("settle_down_score", 0) >= SETTLE_DOWN_MIN_SCORE
        and scores.get("continuation_score", 100) <= CONTINUATION_MAX_FOR_FADE
        and scores.get("current_inning_pressure", 0) <= INFLATED_UNDER_ALLOW_CURRENT_PRESSURE
        and scores.get("contact_quality", 0) <= INFLATED_UNDER_ALLOW_CONTACT
        and confirmation >= 58
        and projection >= 58
    )

    strike_ready = (
        edge <= -min_edge
        and projection >= 62
        and confirmation >= min_confirm
        and scores.get("run_prevention", 0) >= 75
        and scores.get("current_inning_pressure", 0) <= 30
        and scores.get("contact_quality", 0) <= 40
        and (
            scores.get("strikeout_environment", 0) >= MIN_K_ENV_UNDER_STRIKE
            or scores.get("bullpen_lockdown", 0) >= MIN_BULLPEN_LOCKDOWN_UNDER_STRIKE
            or inning <= 4
        )
    )
    if (strike_ready or inflated_fade_ready) and not extreme_blocked:
        return "STRIKE"

    watch_ready = (
        edge <= -MIN_WATCH_EDGE_RUNS
        and projection >= 50
        and confirmation >= 50
    )
    return "WATCH" if watch_ready else "NO_PLAY"


def market_pressure(opening, live):
    if opening is None or live is None:
        return {"move": 0, "direction": "unknown", "abs_move": 0}
    move = round(live - opening, 1)
    if move >= 1:
        direction = "inflated"
    elif move <= -1:
        direction = "suppressed"
    else:
        direction = "stable"
    return {"move": move, "direction": direction, "abs_move": abs(move)}


def expected_future_runs(
    info,
    current_pressure,
    remaining_opp,
    stress,
    dominance,
    contact,
    bullpen,
    lineup,
    suppression,
    false_dom,
    contact_trend=50,
    tto=0,
    starter_exit=0,
    fake_pressure=0,
    market_resistance=0,
    blowout_kill=0,
    under_environment=0,
    run_conversion=0,
    run_prevention=0,
    predictive_market_move=0,
    pressure_to_runs=0,
    pre_run_over_watch=0,
    strikeout_environment=0,
    bullpen_lockdown=0,
    traffic_conversion=0,
    hard_hit_efficiency=0,
    hard_hit_under_support=0,
):
    innings_left = innings_remaining_estimate(info)
    base_rate = innings_left * 0.95

    upward = 0
    downward = 0

    upward += (current_pressure / 100) * 1.00
    upward += (remaining_opp / 100) * 1.25
    upward += (stress / 100) * 0.85
    upward += (contact / 100) * 0.85
    upward += (bullpen / 100) * 0.70
    upward += (lineup / 100) * 0.55
    upward += (suppression / 100) * 0.35
    upward += (false_dom / 100) * 0.40
    upward += (max(0, contact_trend - 50) / 50) * 0.55
    upward += (tto / 100) * 0.50
    upward += (starter_exit / 100) * 0.45
    upward += (max(0, market_resistance) / 100) * 0.60
    upward += (run_conversion / 100) * 0.90
    upward += (pressure_to_runs / 100) * 0.75
    upward += (pre_run_over_watch / 100) * 0.55
    upward += (max(0, predictive_market_move) / 100) * 0.45
    upward += (traffic_conversion / 100) * 0.55
    upward += (hard_hit_efficiency / 100) * 0.45

    downward += (dominance / 100) * 1.20
    downward += (fake_pressure / 100) * 0.75
    downward += (blowout_kill / 100) * 0.70
    downward += (under_environment / 100) * 0.95
    downward += (run_prevention / 100) * 0.90
    downward += (max(0, -market_resistance) / 100) * 0.60
    downward += (max(0, -predictive_market_move) / 100) * 0.35
    downward += (strikeout_environment / 100) * 0.75
    downward += (bullpen_lockdown / 100) * 0.65
    downward += (hard_hit_under_support / 100) * 0.45
    downward += max(0, 70 - current_pressure) * 0.007 if current_pressure < 40 else 0
    downward += max(0, 65 - remaining_opp) * 0.006 if remaining_opp < 45 else 0

    return round(max(0.2, base_rate + upward - downward), 1)


def projected_final_total(info, expected_future):
    return round(info["total_runs"] + expected_future, 1)


def classify_scenario(
    info,
    opening,
    live,
    current_pressure,
    remaining_opp,
    stress,
    dominance,
    contact,
    bullpen,
    lineup,
    suppression,
    false_dom,
    contact_trend=50,
    tto=0,
    starter_exit=0,
    fake_pressure=0,
    market_resistance=0,
    blowout_kill=0,
    under_environment=0,
    strikeout_environment=0,
    bullpen_lockdown=0,
    traffic_conversion=0,
    hard_hit_efficiency=0,
    hard_hit_under_support=0,
):
    mp = market_pressure(opening, live)
    total_runs = info["total_runs"]

    if ENABLE_MARKET_REACTION_ENGINE:
        temp_scores = {
            "current_inning_pressure": current_pressure,
            "remaining_opportunity": remaining_opp,
            "pitcher_stress": stress,
            "dominance": dominance,
            "contact_quality": contact,
            "contact_trend": contact_trend,
            "lineup_pressure": lineup,
            "bullpen_risk": bullpen,
            "starter_exit_probability": starter_exit,
            "fake_pressure": fake_pressure,
            "market_resistance": market_resistance,
            "blowout_kill": blowout_kill,
            "under_environment": under_environment,
            "strikeout_environment": strikeout_environment,
            "bullpen_lockdown": bullpen_lockdown,
            "traffic_conversion": traffic_conversion,
            "hard_hit_efficiency": hard_hit_efficiency,
            "hard_hit_under_support": hard_hit_under_support,
        }
        reaction = market_reaction_scores(info, opening, opening, live, temp_scores)
        profile = reaction.get("market_reaction_profile")
        if profile in ["DISCOUNTED_OVER", "INFLATED_UNDER", "FALSE_INFLATION_FADE", "CONTINUATION_OVER"]:
            return market_reaction_scenario_label(profile)

    # V2.2.1 predictive paths: watch before score/market fully moves.
    # These are not automatic bets; run conversion/prevention determines WATCH vs STRIKE later.
    if contact_trend >= 65 and stress >= 50 and market_resistance >= 0:
        return "Predictive Market Move → Over Watch"

    if lineup >= 65 and remaining_opp >= 55 and (stress >= 45 or contact >= 45) and fake_pressure < 55:
        return "Pre-Run Pressure Build → Over Watch"

    if fake_pressure >= 50 and under_environment >= 55:
        return "Predictive Market Move → Under Watch"

    # V2.4 pro refinement paths from reviewed winners/losers.
    if strikeout_environment >= 70 and bullpen_lockdown >= 55 and current_pressure <= 35:
        return "Strikeout + Bullpen Lockdown → Under Opportunity"

    if traffic_conversion >= 65 and hard_hit_efficiency >= 55 and strikeout_environment < 60 and bullpen_lockdown < 55:
        return "Traffic Not Converted + Hard Contact → Over Opportunity"

    if hard_hit_under_support >= 65 and strikeout_environment >= 60 and current_pressure <= 35:
        return "Weak Contact + Strikeouts → Under Watch"

    # True UNDER paths first. This fixes the prior over-only behavior.
    if mp["direction"] == "inflated" and total_runs >= 3 and under_environment >= 55 and current_pressure <= 40:
        return "Fast Start → Inflated Total → Under Opportunity"

    if dominance >= 70 and contact <= 40 and current_pressure <= 35:
        return "Pitcher Control → Under Strike"

    if fake_pressure >= 55 and dominance >= 55:
        return "Fake Pressure → Under Opportunity"

    if mp["direction"] == "inflated" and dominance >= 55 and contact <= 45 and stress <= 50:
        return "Market Inflated / No Pressure → Under Watch"

    if blowout_kill >= 35:
        return "Blowout Kill Risk → Under Watch"

    # Over paths require more than scoreboard quietness.
    if mp["direction"] == "suppressed" and remaining_opp >= 45:
        if (stress >= 55 or contact >= 50 or contact_trend >= 65 or starter_exit >= 55 or tto >= 45):
            return "Slow Start + Real Pressure → Over Opportunity"
        if suppression >= 55:
            return "Run Suppression → WATCH ONLY"

    if false_dom >= 60 and fake_pressure < 45:
        return "False Dominance → Delayed Collapse Watch"

    if lineup >= 70 and remaining_opp >= 55 and fake_pressure < 50:
        return "Lineup Cycle Pressure"

    if bullpen >= 65 or starter_exit >= 70:
        return "Bullpen Cliff"

    if opening is not None and opening <= 8 and dominance >= 65 and current_pressure <= 35 and stress <= 45:
        return "Strong Pregame Under → Under Continuation"

    if opening is not None and opening >= 9 and (stress >= 60 or contact >= 60 or starter_exit >= 65):
        return "Strong Pregame Over → Over Continuation"

    if suppression >= 60:
        return "Run Suppression → WATCH ONLY"

    if under_environment >= 55:
        return "Dead Contact → Under Watch"

    return "Neutral / Watch"


def scenario_bias(scenario):
    # WATCH ONLY is intentionally not an automatic OVER bias.
    if any(x in scenario for x in ["Under", "UNDER", "Control", "Dead Contact", "Fake Pressure", "Inflated", "Blowout", "Overreaction", "Fade"]):
        return "UNDER"
    if any(x in scenario for x in ["Over Opportunity", "OVER", "Collapse", "Lineup", "Bullpen", "Over Continuation", "Underreaction", "Continuation OVER"]):
        return "OVER"
    return "NONE"


def edge_grade(edge):
    ae = abs(edge)
    if ae >= 2.0:
        return "Rare"
    if ae >= 1.5:
        return "Strong"
    if ae >= 1.0:
        return "Playable"
    if ae >= 0.5:
        return "Watch"
    return "Noise"



def normalize_book_key(title):
    """Normalize sportsbook titles from Odds API into stable names."""
    t = str(title or "").strip().lower()
    aliases = {
        "betmgm": "betmgm",
        "bet mgm": "betmgm",
        "draftkings": "draftkings",
        "draft kings": "draftkings",
        "fanduel": "fanduel",
        "fan duel": "fanduel",
        "caesars": "caesars",
        "caesars sportsbook": "caesars",
        "espn bet": "espnbet",
        "espnbet": "espnbet",
        "bet365": "bet365",
        "fanatics": "fanatics",
    }
    return aliases.get(t, t.replace(" ", ""))


def choose_primary_book_total(book_totals):
    """
    Pick a practical live total from book-level data.
    Prefer books in PREFERRED_BOOKS; otherwise use the first available book.
    """
    if not book_totals:
        return {"point": None, "over_price": None, "under_price": None, "book": None}
    by_key = {normalize_book_key(b.get("book")): b for b in book_totals}
    for preferred in PREFERRED_BOOKS:
        if preferred in by_key:
            return by_key[preferred]
    return book_totals[0]


def book_price_ok(price):
    try:
        return price is None or (MAX_PRICE_FAVORITE <= int(price) <= MAX_PRICE_DOG)
    except Exception:
        return False


def price_penalty_ticks(price):
    """
    Small practical penalty for worse live-app prices.
    Used only to choose between nearby available lines; it does not replace price_ok().
    """
    if price is None:
        return 8
    p = abs(safe_int(price, 999))
    if p <= 105:
        return 0
    if p <= 115:
        return 4
    if p <= 125:
        return 8
    if p <= 135:
        return 14
    return 22


def best_available_for_side(book_totals, side):
    """
    Raw best playable line. For OVER, lower point is best. For UNDER, higher
    point is best. V3.7.1 filters to non-ignored and user-playable books.
    """
    side = str(side or "").upper()
    candidates = []
    for b in remove_ignored_book_totals(book_totals or []):
        point = safe_float(b.get("point"), None)
        if point is None:
            continue
        price = b.get("over_price") if side == "OVER" else b.get("under_price")
        if not book_price_ok(price):
            continue
        candidates.append({**b, "side_price": price})

    candidates = playable_book_filter_candidates(candidates)
    if not candidates:
        return None

    if side == "OVER":
        return sorted(candidates, key=lambda x: (safe_float(x.get("point"), 99), price_penalty_ticks(x.get("side_price"))))[0]
    if side == "UNDER":
        return sorted(candidates, key=lambda x: (-safe_float(x.get("point"), -99), price_penalty_ticks(x.get("side_price"))))[0]
    return None


def price_adjusted_best_available_for_side(book_totals, side):
    """
    Practical best line for real apps. V3.7.1 hardens this so the selected
    line must come from a user-playable book when REQUIRE_PLAYABLE_BOOK_FOR_STRIKE
    is enabled. This prevents DraftKings/FanDuel/Caesars from becoming the
    recommended line when the user can only act at BetMGM.
    """
    side = str(side or "").upper()
    candidates = []
    for b in remove_ignored_book_totals(book_totals or []):
        point = safe_float(b.get("point"), None)
        if point is None:
            continue
        price = b.get("over_price") if side == "OVER" else b.get("under_price")
        if not book_price_ok(price):
            continue
        candidates.append({**b, "side_price": price})

    candidates = playable_book_filter_candidates(candidates)
    if not candidates:
        return None

    if side == "OVER":
        best_raw_point = min(safe_float(c.get("point"), 99) for c in candidates)
        def score(c):
            half_runs_worse = max(0, (safe_float(c.get("point"), best_raw_point) - best_raw_point) / 0.5)
            return half_runs_worse * PRICE_ADJUSTED_HALF_RUN_VALUE + price_penalty_ticks(c.get("side_price"))
        return sorted(candidates, key=lambda c: (score(c), safe_float(c.get("point"), 99)))[0]

    if side == "UNDER":
        best_raw_point = max(safe_float(c.get("point"), -99) for c in candidates)
        def score(c):
            half_runs_worse = max(0, (best_raw_point - safe_float(c.get("point"), best_raw_point)) / 0.5)
            return half_runs_worse * PRICE_ADJUSTED_HALF_RUN_VALUE + price_penalty_ticks(c.get("side_price"))
        return sorted(candidates, key=lambda c: (score(c), -safe_float(c.get("point"), -99)))[0]

    return None


def market_snapshot(book_totals, primary_total=None):
    # V3.6: consensus/confirmation should be shaped by major market books first.
    market_books = market_reference_book_totals(book_totals or [])
    pts = [safe_float(b.get("point"), None) for b in (market_books or [])]
    pts = [x for x in pts if x is not None]
    if not pts:
        return {
            "book_count": 0,
            "consensus_total": primary_total,
            "market_min_total": primary_total,
            "market_max_total": primary_total,
            "market_disagreement": 0,
            "book_totals": [],
        }
    consensus = round(sum(pts) / len(pts), 2)
    return {
        "book_count": len(pts),
        "consensus_total": consensus,
        "market_min_total": min(pts),
        "market_max_total": max(pts),
        "market_disagreement": round(max(pts) - min(pts), 2),
        "book_totals": book_totals or [],
    }


def find_markets(odds_events, home, away):
    mlb_home = clean_team(home)
    mlb_away = clean_team(away)

    empty = {
        "total": {"point": None, "over_price": None, "under_price": None, "book": None},
        "book_totals": [],
        "book_totals_by_name": {},
        "team_totals": [],
        "remaining_totals": [],
        "market": market_snapshot([], None),
    }

    for ev in odds_events:
        odds_home = clean_team(ev.get("home_team"))
        odds_away = clean_team(ev.get("away_team"))

        if odds_home != mlb_home or odds_away != mlb_away:
            continue

        result = json.loads(json.dumps(empty))
        book_totals = []

        for book in ev.get("bookmakers", []):
            book_title = book.get("title") or book.get("key") or "Unknown"
            if is_ignored_recommendation_book(book_title):
                print(f"BOOK FILTER | ignored book removed from market math: {book_title}")
                continue
            book_key = normalize_book_key(book_title)
            for market in book.get("markets", []):
                key = market.get("key")

                if key == "totals":
                    bt = {"book": book_title, "book_key": book_key, "point": None, "over_price": None, "under_price": None, "last_update": market.get("last_update")}
                    for out in market.get("outcomes", []):
                        if out.get("name") == "Over":
                            bt["point"] = out.get("point")
                            bt["over_price"] = out.get("price")
                        elif out.get("name") == "Under":
                            bt["point"] = out.get("point")
                            bt["under_price"] = out.get("price")
                    if bt["point"] is not None:
                        book_totals.append(bt)

                elif key in ["team_totals", "alternate_team_totals"]:
                    grouped = {}
                    for out in market.get("outcomes", []):
                        team = out.get("description") or out.get("team") or out.get("name")
                        grouped.setdefault(team, {"team": team, "point": out.get("point"), "over_price": None, "under_price": None, "book": book_title})
                        if out.get("name") == "Over":
                            grouped[team]["over_price"] = out.get("price")
                            grouped[team]["point"] = out.get("point")
                        elif out.get("name") == "Under":
                            grouped[team]["under_price"] = out.get("price")
                            grouped[team]["point"] = out.get("point")
                    result["team_totals"].extend([v for v in grouped.values() if v.get("point") is not None])

                elif key in ["remaining_totals", "live_totals", "game_remaining_totals"]:
                    rem = {"point": None, "over_price": None, "under_price": None, "book": book_title}
                    for out in market.get("outcomes", []):
                        if out.get("name") == "Over":
                            rem["point"] = out.get("point")
                            rem["over_price"] = out.get("price")
                        elif out.get("name") == "Under":
                            rem["point"] = out.get("point")
                            rem["under_price"] = out.get("price")
                    if rem["point"] is not None:
                        result["remaining_totals"].append(rem)

        primary = choose_primary_book_total(book_totals)
        result["total"] = {
            "point": primary.get("point"),
            "over_price": primary.get("over_price"),
            "under_price": primary.get("under_price"),
            "book": primary.get("book"),
        }
        book_totals = remove_ignored_book_totals(book_totals)
        result["book_totals"] = book_totals
        result["book_totals_by_name"] = {b.get("book_key"): b for b in book_totals}
        result["market"] = market_snapshot(book_totals, result["total"].get("point"))
        return result

    print(f"NO ODDS MATCH FOR: {away} at {home}")
    return empty


def update_line_velocity_state(state_game, markets):
    """
    V3.2: track market velocity overall and per book using already-fetched odds.
    No extra API calls. This tells us whether one book moved first or the whole
    market moved together.
    """
    if not ENABLE_MARKET_INTELLIGENCE:
        return {"line_velocity": 0, "line_velocity_abs": 0, "line_direction": "flat", "history_count": 0, "book_velocities": {}, "leading_book": None}
    market_books = market_reference_book_totals((markets or {}).get("book_totals", []) or [])
    point = safe_float(((markets or {}).get("market") or {}).get("consensus_total"), None)
    if point is None:
        point = safe_float(((markets or {}).get("total") or {}).get("point"), None)
    if point is None:
        return {"line_velocity": 0, "line_velocity_abs": 0, "line_direction": "unknown", "history_count": 0, "book_velocities": {}, "leading_book": None}
    now_ts = time.time()

    hist = state_game.setdefault("line_history", [])
    hist.append({"ts": now_ts, "point": point})
    cutoff = now_ts - MARKET_VELOCITY_WINDOW_SECONDS
    hist[:] = [h for h in hist if safe_float(h.get("ts"), 0) >= cutoff]
    if len(hist) < 2:
        overall_move = 0
        direction = "flat"
    else:
        oldest = hist[0]
        overall_move = round(point - safe_float(oldest.get("point"), point), 2)
        direction = "up" if overall_move > 0 else "down" if overall_move < 0 else "flat"

    # Per-book velocities.
    book_hist = state_game.setdefault("book_line_history", {})
    book_velocities = {}
    for b in market_books:
        key = b.get("book_key") or normalize_book_key(b.get("book"))
        b_point = safe_float(b.get("point"), None)
        if not key or b_point is None:
            continue
        bh = book_hist.setdefault(key, [])
        bh.append({"ts": now_ts, "point": b_point, "book": b.get("book")})
        bh[:] = [h for h in bh if safe_float(h.get("ts"), 0) >= cutoff]
        if len(bh) >= 2:
            b_move = round(b_point - safe_float(bh[0].get("point"), b_point), 2)
        else:
            b_move = 0
        book_velocities[key] = {"book": b.get("book"), "move": b_move, "direction": "up" if b_move > 0 else "down" if b_move < 0 else "flat"}

    leading = None
    if book_velocities:
        leading = max(book_velocities.values(), key=lambda x: abs(safe_float(x.get("move"), 0)))
        if abs(safe_float(leading.get("move"), 0)) == 0:
            leading = None

    return {
        "line_velocity": overall_move,
        "line_velocity_abs": abs(overall_move),
        "line_direction": direction,
        "history_count": len(hist),
        "book_velocities": book_velocities,
        "book_velocity_summary": "; ".join([f"{v.get('book')} {safe_float(v.get('move'),0):+.1f}" for v in book_velocities.values() if abs(safe_float(v.get('move'),0)) >= 0.5])[:180],
        "leading_book": (leading or {}).get("book"),
    }

def market_confirmation_score(side, market_info, velocity_info, opening_total=None, live_total=None):
    """
    Score whether the betting market confirms the baseball signal.
    OVER likes upward velocity and/or an available lower-than-consensus number.
    UNDER likes downward velocity and/or an available higher-than-consensus number.
    """
    if not ENABLE_MARKET_INTELLIGENCE:
        return 50
    side = str(side or "").upper()
    market_info = market_info or {}
    velocity_info = velocity_info or {}
    book_count = safe_int(market_info.get("book_count"), 0)
    if book_count <= 0:
        return 50

    score = 45
    disagreement = safe_float(market_info.get("market_disagreement"), 0)
    consensus = safe_float(market_info.get("consensus_total"), live_total)
    best = market_info.get("best_for_side") or {}
    best_point = safe_float(best.get("point"), live_total)
    line_velocity = safe_float(velocity_info.get("line_velocity"), 0)

    # More books = better market read.
    score += min(15, book_count * 4)

    if side == "OVER":
        if line_velocity > 0:
            score += min(20, line_velocity * 14)
        elif line_velocity < 0:
            score -= min(25, abs(line_velocity) * 16)
        if best_point is not None and consensus is not None and best_point <= consensus - 0.25:
            score += 10
    elif side == "UNDER":
        if line_velocity < 0:
            score += min(20, abs(line_velocity) * 14)
        elif line_velocity > 0:
            score -= min(25, line_velocity * 16)
        if best_point is not None and consensus is not None and best_point >= consensus + 0.25:
            score += 10

    if disagreement >= MARKET_DISAGREEMENT_STRONG:
        score += 8

    # V3.4: modestly reward a preferred leading book moving in the same direction.
    lbs = leading_book_score(side, velocity_info)
    score += (lbs - 50) * 0.35
    if opening_total is not None and live_total is not None:
        move_from_open = safe_float(live_total, 0) - safe_float(opening_total, 0)
        if side == "OVER" and move_from_open > 0:
            score += min(10, move_from_open * 3)
        if side == "UNDER" and move_from_open < 0:
            score += min(10, abs(move_from_open) * 3)

    return round(clamp(score))


def live_evidence_report(info, p, q, traffic, scores, scenario, side=None):
    """
    Prevents false STRIKE alerts caused only by lineup pressure or full game opportunity
    before real live baseball evidence exists. V2.1 allows different OVER/UNDER evidence.
    """
    total_pitches = q.get("total_pitches", 0)
    balls_in_play = q.get("balls_in_play", 0)
    pitch_count = p.get("pitch_count", 0)
    batters_faced = p.get("batters_faced", 0)

    signals = []
    real_signal_count = 0

    if side == "UNDER":
        checks = [
            ("pitcher dominance", scores.get("dominance", 0) >= 55),
            ("pitching dominance under", scores.get("pitching_dominance_under_score", 0) >= PITCHING_DOMINANCE_MIN_SCORE),
            ("dead contact", scores.get("contact_quality", 100) <= 35 and balls_in_play >= 4),
            ("low inning pressure", scores.get("current_inning_pressure", 100) <= 35),
            ("fake pressure", scores.get("fake_pressure", 0) >= 45),
            ("under environment", scores.get("under_environment", 0) >= 50),
            ("run prevention", scores.get("run_prevention", 0) >= 60),
            ("predictive under move", scores.get("predictive_market_move", 0) <= -MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH),
            ("market inflated", scores.get("market_resistance", 0) <= -20),
            ("limited remaining opportunity", scores.get("remaining_opportunity", 100) <= 40),
        ]
    else:
        checks = [
            ("pitcher stress", scores.get("pitcher_stress", 0) >= 50),
            ("contact quality", scores.get("contact_quality", 0) >= 50),
            ("rising contact trend", scores.get("contact_trend", 50) >= 65),
            ("current inning pressure", scores.get("current_inning_pressure", 0) >= 60),
            ("bullpen risk", scores.get("bullpen_risk", 0) >= 55),
            ("starter exit risk", scores.get("starter_exit_probability", 0) >= 55),
            ("run conversion", scores.get("run_conversion", 0) >= 60),
            ("pressure to runs", scores.get("pressure_to_runs", 0) >= MIN_OVER_RUN_CONVERSION_FOR_WATCH),
            ("pre-run over watch", scores.get("pre_run_over_watch", 0) >= PRE_RUN_OVER_WATCH_SCORE),
            ("predictive market move", scores.get("predictive_market_move", 0) >= MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH),
            ("times through order", scores.get("times_through_order", 0) >= 45),
            ("false dominance", scores.get("false_dominance", 0) >= 50),
            ("recent traffic", traffic.get("recent_baserunners", 0) >= 3),
            ("consecutive baserunners", traffic.get("consecutive_baserunners", 0) >= 2),
        ]

    for label, passed in checks:
        if passed:
            real_signal_count += 1
            signals.append(label)

    if pitch_count == 0 or batters_faced == 0:
        return {"ok": False, "reason": "no live pitching data yet", "real_signal_count": real_signal_count, "signals": signals}

    if scenario == "Neutral / Watch" and info.get("inning", 1) < MIN_INNING_FOR_NEUTRAL_ALERT:
        return {"ok": False, "reason": "neutral scenario is watch-only", "real_signal_count": real_signal_count, "signals": signals}

    # Do not force late OVERs in the 8th/9th unless they are very strong.
    if side == "OVER" and info.get("inning", 1) >= MAX_LATE_OVER_INNING and scores.get("current_inning_pressure", 0) < 65:
        return {"ok": False, "reason": "late over blocked without current pressure", "real_signal_count": real_signal_count, "signals": signals}

    if total_pitches < MIN_LIVE_PITCHES_FOR_STRIKE and balls_in_play < MIN_BALLS_IN_PLAY_FOR_STRIKE:
        return {"ok": False, "reason": "not enough pitches or balls in play", "real_signal_count": real_signal_count, "signals": signals}

    if real_signal_count < MIN_REAL_SIGNAL_COUNT:
        return {"ok": False, "reason": "not enough independent live signals", "real_signal_count": real_signal_count, "signals": signals}

    return {"ok": True, "reason": "live evidence confirmed", "real_signal_count": real_signal_count, "signals": signals}


def detect_total_opportunity(market, info, projected_total, scenario, scores, p, q, traffic, state=None):
    state = state or {"games": {}}
    live = market.get("point")
    over_price = market.get("over_price")
    under_price = market.get("under_price")

    if live is None:
        return None

    base_scores = dict(scores or {})
    pd_scores = pitching_dominance_under_scores(
        info,
        market.get("opening_total"),
        market.get("first_seen_total") or market.get("opening_total"),
        live,
        base_scores,
        p,
        q,
        traffic,
    )
    base_scores.update(pd_scores)
    if pd_scores.get("pitching_dominance_under_ok"):
        base_scores["market_reaction_profile"] = "PITCHING_DOMINANCE_UNDER"

    projected_total = adjusted_projection_for_time(info, live, projected_total)
    projected_total = market_reaction_projection_adjustment(info, live, projected_total, base_scores)
    edge = round(projected_total - live, 1)
    bias = scenario_bias(scenario)

    candidates = []

    def add_candidate(side, line, price, cand_edge, cand_scenario, force_action=None, profile_override=None):
        nonlocal candidates
        side_scores = dict(base_scores)
        if profile_override:
            side_scores["market_reaction_profile"] = profile_override
        gate_ok, gate_reason = market_reaction_side_gate(side, cand_edge, side_scores, info)
        if not gate_ok:
            side_scores = annotate_market_reaction_block(side_scores, gate_reason)
            tmp_opp = {"side": side, "line": line, "price": price, "edge": round(cand_edge, 1), "scenario": cand_scenario, "scores": side_scores, "confidence": "", "action": "REJECTED", "projected_total": projected_total}
            log_profile_near_miss(info, tmp_opp, gate_reason)
            log_profile_research_candidate(info, tmp_opp, gate_reason, market, promoted=False)
            return

        evidence = live_evidence_report(info, p, q, traffic, side_scores, cand_scenario, side=side)
        if not evidence["ok"]:
            tmp_opp = {"side": side, "line": line, "price": price, "edge": round(cand_edge, 1), "scenario": cand_scenario, "scores": side_scores, "confidence": "", "action": "REJECTED", "projected_total": projected_total}
            reject_reason = evidence.get("reason", "live evidence rejected")
            log_profile_near_miss(info, tmp_opp, reject_reason)
            log_profile_research_candidate(info, tmp_opp, reject_reason, market, promoted=False)
            return

        side_scores["inning"] = safe_int(info.get("inning", 1), 1)
        side_scores = apply_winner_pattern_enhancements(state, info, side, side_scores, line, projected_total)
        side_scores["projection_score"] = projection_score(side, cand_edge, side_scores)
        side_scores["confirmation_score"] = confirmation_score(side, info, side_scores)
        confidence = confidence_score(side, cand_edge, cand_scenario, side_scores, evidence, side_scores.get("market_resistance", 0))
        confidence = min(confidence, max(side_scores["projection_score"], side_scores["confirmation_score"]))
        profile = market_reaction_profile_from_scores(side_scores, cand_scenario)
        profile_learning = profile_learning_adjustment(profile, side)
        confidence = round(clamp(confidence - safe_int(profile_learning.get("confidence_adjustment"), 0)))
        action = force_action or action_from_confidence(side, confidence, cand_edge, side_scores)
        if action == "NO_PLAY":
            tmp_opp = {"side": side, "line": line, "price": price, "edge": round(cand_edge, 1), "scenario": cand_scenario, "scores": side_scores, "confidence": confidence, "action": action, "projected_total": projected_total}
            log_profile_near_miss(info, tmp_opp, "action_from_confidence returned NO_PLAY")
            log_profile_research_candidate(info, tmp_opp, "action_from_confidence returned NO_PLAY", market, promoted=False)
            return

        candidate = {
            "market_type": "Full Game Total",
            "side": side,
            "line": line,
            "price": price,
            "edge": round(cand_edge, 1),
            "edge_grade": edge_grade(cand_edge),
            "scenario": clean_scenario_label(cand_scenario),
            "scores": side_scores,
            "projected_total": projected_total,
            "projection": projected_total,
            "evidence": evidence,
            "confidence": confidence,
            "action": action,
            "market_reaction_profile": profile,
            "profile_status": profile_learning.get("profile_status"),
            "profile_sample": profile_learning.get("sample"),
            "profile_win_pct": profile_learning.get("win_pct"),
            "profile_avg_clv": profile_learning.get("avg_clv"),
            "profile_confidence_adjustment": profile_learning.get("confidence_adjustment"),
        }
        test_ok, test_reason = under_profile_test_alert_reason(info, candidate, side_scores)
        if test_ok:
            candidate["action"] = "STRIKE"
            candidate["under_profile_test_alert"] = True
            candidate["profile_promotion_reason"] = test_reason
            if UNDER_TEST_FORCE_TIER_C:
                candidate["calculated_risk_tier"] = "C"
                candidate["suggested_unit"] = UNDER_TEST_UNIT_LABEL
        candidate["calculated_risk_tier"] = candidate.get("calculated_risk_tier") or calculated_risk_tier(candidate)
        if is_discounted_over_tier_a(info, candidate, side_scores):
            candidate["calculated_risk_tier"] = "A"
        candidate["suggested_unit"] = candidate.get("suggested_unit") or tier_unit_guidance(candidate["calculated_risk_tier"])
        late_block, late_reason = should_block_late_discounted_over(info, candidate, side_scores)
        if late_block:
            candidate["action"] = "WATCH"
            candidate["discounted_over_late_watch_only"] = True
            candidate["profile_promotion_reason"] = late_reason
            log_profile_near_miss(info, candidate, late_reason, market)
            log_profile_research_candidate(info, candidate, late_reason, market, promoted=False)
            return
        log_profile_research_candidate(info, candidate, "candidate_created", market, promoted=(candidate.get("action") == "STRIKE"))
        candidates.append(candidate)

    # Standard OVER path.
    if edge >= MIN_WATCH_EDGE_RUNS and price_ok(over_price, edge):
        add_candidate("OVER", live, over_price, edge, scenario)

    # Standard UNDER path.
    if edge <= -MIN_WATCH_EDGE_RUNS and price_ok(under_price, abs(edge)):
        add_candidate("UNDER", live, under_price, edge, scenario)

    # V3.7.1 Inflated UNDER / False Inflation Fade:
    # This is a market-overreaction path. It can create an UNDER even when the
    # old classic UNDER engine would have been too tight, but only if the
    # side-gate says the market reaction is genuinely settling.
    if ENABLE_MARKET_REACTION_ENGINE and base_scores.get("market_reaction_profile") in ["INFLATED_UNDER", "FALSE_INFLATION_FADE"]:
        research_opp = {"side": "UNDER", "line": live, "price": under_price, "edge": round(edge, 1), "scenario": market_reaction_scenario_label(base_scores.get("market_reaction_profile")), "scores": dict(base_scores), "confidence": "", "action": "CANDIDATE", "projected_total": projected_total}
        log_profile_research_candidate(info, research_opp, "inflated_under_profile_detected", market, promoted=False)
        inflated_test_edge_ok = edge <= -min(INFLATED_UNDER_MIN_EDGE, INFLATED_UNDER_TEST_MIN_EDGE)
        if inflated_test_edge_ok and price_ok(under_price, abs(edge)):
            add_candidate(
                "UNDER",
                live,
                under_price,
                edge,
                market_reaction_scenario_label(base_scores.get("market_reaction_profile")),
                force_action="STRIKE",
            )

    # V3.7.2 Pitching Dominance UNDER:
    # This is an early calculated-risk UNDER path for low-open games where true
    # suppression is showing before the live total fully collapses.
    if ENABLE_PITCHING_DOMINANCE_UNDER and safe_int(base_scores.get("pitching_dominance_under_score"), 0) >= PROFILE_RESEARCH_PITCHING_MIN_SCORE:
        pd_research_edge = edge if edge <= 0 else -PITCHING_DOMINANCE_MIN_EDGE
        pd_scores = dict(base_scores, market_reaction_profile="PITCHING_DOMINANCE_UNDER")
        pd_research_opp = {"side": "UNDER", "line": live, "price": under_price, "edge": round(pd_research_edge, 1), "scenario": market_reaction_scenario_label("PITCHING_DOMINANCE_UNDER"), "scores": pd_scores, "confidence": "", "action": "CANDIDATE", "projected_total": projected_total}
        log_profile_research_candidate(info, pd_research_opp, "pitching_dominance_candidate_score_threshold", market, promoted=False)

        pd_score = safe_int(base_scores.get("pitching_dominance_under_score"), 0)
        pd_edge = edge if edge <= 0 else -PITCHING_DOMINANCE_TEST_MIN_EDGE
        if pd_score >= PITCHING_DOMINANCE_TEST_MIN_SCORE and price_ok(under_price, abs(pd_edge)):
            add_candidate(
                "UNDER",
                live,
                under_price,
                pd_edge,
                market_reaction_scenario_label("PITCHING_DOMINANCE_UNDER"),
                force_action="STRIKE",
                profile_override="PITCHING_DOMINANCE_UNDER",
            )

    if ENABLE_PITCHING_DOMINANCE_UNDER and base_scores.get("pitching_dominance_under_ok"):
        pd_edge = edge
        if pd_edge > -PITCHING_DOMINANCE_MIN_EDGE:
            pd_edge = -PITCHING_DOMINANCE_MIN_EDGE
        if price_ok(under_price, abs(pd_edge)):
            add_candidate(
                "UNDER",
                live,
                under_price,
                pd_edge,
                market_reaction_scenario_label("PITCHING_DOMINANCE_UNDER"),
                force_action="STRIKE",
                profile_override="PITCHING_DOMINANCE_UNDER",
            )

    # Predictive market move WATCH: pressure building before market fully moves.
    if SEND_WATCH_ALERTS and not candidates:
        predictive = base_scores.get("predictive_market_move", 0)
        if predictive >= MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH and price_ok(over_price, abs(edge) if edge else MIN_WATCH_EDGE_RUNS):
            watch_edge = max(edge, MIN_WATCH_EDGE_RUNS)
            add_candidate("OVER", live, over_price, watch_edge, "Predictive Market Move → Over Watch", force_action="WATCH")
        elif predictive <= -MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH and price_ok(under_price, abs(edge) if edge else MIN_WATCH_EDGE_RUNS):
            watch_edge = min(edge, -MIN_WATCH_EDGE_RUNS)
            add_candidate("UNDER", live, under_price, watch_edge, "Predictive Market Move → Under Watch", force_action="WATCH")

    # Dedicated V2.2.1 Pre-Run OVER WATCH:
    if SEND_WATCH_ALERTS and not candidates and base_scores.get("pre_run_over_watch", 0) >= PRE_RUN_OVER_WATCH_SCORE:
        watch_edge = max(edge, MIN_WATCH_EDGE_RUNS)
        if base_scores.get("pressure_to_runs", 0) >= MIN_OVER_RUN_CONVERSION_FOR_WATCH:
            add_candidate("OVER", live, over_price, watch_edge, "Pre-Run Pressure Build → Over Watch", force_action="WATCH")

    if not candidates:
        return None

    # Scenario bias can filter weak conflicts, but not strong confidence/edge.
    filtered = []
    seen = set()
    for c in candidates:
        key = (c.get("side"), c.get("line"), c.get("scenario"), c.get("action"))
        if key in seen:
            continue
        seen.add(key)

        if bias != "NONE" and bias != c["side"] and abs(c["edge"]) < STRONG_EDGE_RUNS and c["confidence"] < 72:
            continue
        if c["action"] == "WATCH" and not SEND_WATCH_ALERTS:
            continue
        filtered.append(c)

    if not filtered:
        return None

    # Prefer STRIKE over WATCH, then highest confidence, then strongest market-reaction fit.
    def rank(c):
        s = c.get("scores", {}) or {}
        side = c.get("side")
        profile_bonus = 0
        if side == "OVER" and s.get("market_reaction_profile") in ["DISCOUNTED_OVER", "CONTINUATION_OVER"]:
            profile_bonus += 8
        if side == "UNDER" and s.get("market_reaction_profile") in ["INFLATED_UNDER", "FALSE_INFLATION_FADE", "PITCHING_DOMINANCE_UNDER"]:
            profile_bonus += 8
        return (1 if c["action"] == "STRIKE" else 0, c["confidence"], profile_bonus, abs(c["edge"]))

    filtered.sort(key=rank, reverse=True)
    return filtered[0]


def should_alert(state_game, opportunity):
    """
    Technical send guard only. The betting decision is made by v26_final_betnow_gate().
    This function only prevents exact duplicate sends and records the send event.
    """
    if not opportunity or opportunity.get("action") != "STRIKE":
        return False

    now_ts = time.time()
    alerts = state_game.setdefault("alerts", [])
    side = str(opportunity.get("side", "")).upper()
    line = opportunity.get("line")

    for a in reversed(alerts):
        if a.get("action") == "STRIKE" and a.get("side") == side and a.get("line") == line:
            return False

    if V26_ONE_BET_NOW_PER_GAME:
        prior_strikes = [a for a in alerts if a.get("action") == "STRIKE"]
        if prior_strikes and not opportunity.get("reversal"):
            return False

    alerts.append({
        "ts": now_ts,
        "side": side,
        "line": line,
        "price": opportunity.get("price"),
        "action": "STRIKE",
        "confidence": opportunity.get("confidence"),
        "edge_abs": abs(safe_float(opportunity.get("edge"), 0)),
        "scenario": clean_scenario_label(opportunity.get("scenario", "")),
        "reversal": bool(opportunity.get("reversal")),
    })
    return True


def describe_reasons(info, p, q, traffic, hitters, scores, scenario):
    reasons = []

    reasons.append(f"{info['base_state']['label']}, {info['outs']} out(s), {info['inning_state']} {info['inning']}")

    if scores["current_inning_pressure"] >= 65:
        reasons.append(f"Current inning pressure is elevated ({scores['current_inning_pressure']}/100)")
    elif scores["current_inning_pressure"] <= 30:
        reasons.append(f"Current inning pressure is low ({scores['current_inning_pressure']}/100)")

    if scores["remaining_opportunity"] >= 65:
        reasons.append(f"Remaining scoring opportunity is strong ({scores['remaining_opportunity']}/100)")
    elif scores["remaining_opportunity"] <= 35:
        reasons.append(f"Remaining scoring opportunity is limited ({scores['remaining_opportunity']}/100)")

    if scores["pitcher_stress"] >= 60:
        outs = p.get("outs_recorded", 0)
        ppo = round(p["pitch_count"] / outs, 2) if outs else p["pitch_count"]
        reasons.append(f"Pitcher stress is high: {p['pitch_count']} pitches, {ppo} pitches/out")
    elif scores["dominance"] >= 65:
        reasons.append(f"Pitcher appears in control: strike {q['strike_pct']}%, whiff {q['whiff_pct']}%, CSW {q['csw_pct']}%")

    if scores["contact_quality"] >= 55:
        reasons.append(f"Contact quality is dangerous: {q['hard_hit']} hard-hit, {q['barrels']} barrel(s), max EV {q['max_ev']}")

    if traffic["recent_baserunners"] >= 3:
        reasons.append(f"Recent traffic: {traffic['recent_baserunners']} baserunners in recent plate appearances")

    if traffic["consecutive_baserunners"] >= 2:
        reasons.append(f"Consecutive baserunners signal inning stress ({traffic['consecutive_baserunners']} straight)")

    if scores["lineup_pressure"] >= 65:
        reasons.append(f"Lineup pressure: {format_hitters(hitters)}")

    if scores["bullpen_risk"] >= 60:
        reasons.append(f"Bullpen/transition risk is elevated ({scores['bullpen_risk']}/100)")

    if scores.get("starter_exit_probability", 0) >= 60:
        reasons.append(f"Starter exit risk is high ({scores['starter_exit_probability']}/100)")

    if scores.get("times_through_order", 0) >= 45:
        reasons.append(f"Times-through-order pressure is building ({scores['times_through_order']}/100)")

    if scores.get("fake_pressure", 0) >= 50:
        reasons.append(f"Fake pressure filter is active ({scores['fake_pressure']}/100)")

    if scores.get("under_environment", 0) >= 55:
        reasons.append(f"Under environment is active ({scores['under_environment']}/100)")

    if scores.get("pressure_to_runs", 0) >= MIN_OVER_RUN_CONVERSION_FOR_WATCH:
        reasons.append(f"Pressure-to-runs supports OVER ({scores['pressure_to_runs']}/100)")

    if scores.get("pre_run_over_watch", 0) >= PRE_RUN_OVER_WATCH_SCORE:
        reasons.append(f"Pre-run OVER watch is active ({scores['pre_run_over_watch']}/100)")

    if scores.get("run_conversion", 0) >= 60:
        reasons.append(f"Run conversion supports OVER ({scores['run_conversion']}/100)")

    if scores.get("run_prevention", 0) >= 60:
        reasons.append(f"Run prevention supports UNDER ({scores['run_prevention']}/100)")

    if abs(scores.get("predictive_market_move", 0)) >= MIN_PREDICTIVE_MARKET_MOVE_FOR_WATCH:
        direction = "OVER" if scores.get("predictive_market_move", 0) > 0 else "UNDER"
        reasons.append(f"Predictive market move signal supports {direction} ({scores['predictive_market_move']}/100)")

    if scores.get("market_resistance", 0) >= 25:
        reasons.append(f"Market resistance supports OVER (+{scores['market_resistance']})")
    elif scores.get("market_resistance", 0) <= -25:
        reasons.append(f"Market resistance supports UNDER ({scores['market_resistance']})")

    if scores.get("market_reaction_profile"):
        reasons.append(
            f"Market reaction: {scores.get('market_reaction_profile')} | "
            f"move {safe_float(scores.get('market_reaction_move'), 0):+.1f} | "
            f"settle {scores.get('settle_down_score', 0)}/100 | "
            f"continuation {scores.get('continuation_score', 0)}/100"
        )

    if q["velo_drop"] >= 1.0:
        reasons.append(f"Velocity drop detected: {q['velo_drop']} mph")
    if q["spin_drop"] >= 150:
        reasons.append(f"Spin drop detected: {q['spin_drop']} rpm")
    if q["movement_drop"] >= 2:
        reasons.append(f"Movement drop detected: {q['movement_drop']}")

    if "Predictive Market Move" in scenario:
        reasons.append("Interpretation: leading indicators are building before the market fully adjusts")
    elif "Slow Start" in scenario:
        reasons.append("Interpretation: scoreboard is quiet, but pressure suggests scoring may be delayed")
    elif "Inflated" in scenario:
        reasons.append("Interpretation: live total may have overreacted to early scoring")
    elif "False Dominance" in scenario:
        reasons.append("Interpretation: pitcher may be surviving traffic rather than controlling the game")
    elif "Bullpen Cliff" in scenario:
        reasons.append("Interpretation: starter-to-bullpen transition may change the scoring environment")
    elif "Lineup Cycle" in scenario:
        reasons.append("Interpretation: upcoming hitters improve the scoring environment")

    return reasons[:9]


def format_alert(label, start_label, info, market_context, opportunity, p, q, traffic, hitters, flags):
    scores = opportunity["scores"]
    reasons = describe_reasons(info, p, q, traffic, hitters, scores, opportunity["scenario"])
    reason_text = "\n".join([f"• {r}" for r in reasons])

    flag_text = " | ".join(flags) if flags else "No pitch-type red flags"

    edge_sign = "+" if opportunity["edge"] > 0 else ""
    price_text = opportunity["price"] if opportunity["price"] is not None else "N/A"
    action = opportunity.get("action", "STRIKE")
    instruction = "BET NOW" if action == "STRIKE" else "WATCH ONLY - wait for better price/confirmation"
    entry_guidance = best_entry_guidance(
        opportunity.get("side"),
        opportunity.get("line"),
        market_context.get("opening_total"),
        opportunity.get("edge", 0),
        action,
        scores,
    )
    history_note = historical_pattern_note(info, opportunity)

    return (
        f"{'🔄 SHIFT REVERSAL — BET NOW' if opportunity.get('reversal') else '🚨 SHIFT MLB V3.4 PROFESSIONAL STRIKE — BET NOW'}\n\n"
        f"{label}\n"
        f"Start: {start_label}\n\n"
        f"Instruction:\n"
        f"BET NOW\n\n"
        f"Scenario:\n"
        f"{opportunity['scenario']}\n"
        f"{'Previous Thesis: ' + thesis_summary_text(opportunity.get('previous_thesis')) + chr(10) if opportunity.get('reversal') else ''}\n"
        f"Market:\n"
        f"{opportunity['market_type']}\n\n"
        f"PLAY:\n"
        f"{opportunity['side']} {opportunity['line']} ({price_text})\n"
        f"Price Label: {market_label(opportunity['price'])}\n"
        f"Edge Grade: {opportunity['edge_grade']}\n"
        f"Confidence: {opportunity.get('confidence', 'N/A')}/100\n\n"
        f"Open: {market_context.get('opening_total')}\n"
        f"Live Line: {opportunity['line']}\n"
        f"Proj: {opportunity['projected_total']}\n"
        f"Edge: {edge_sign}{opportunity['edge']} runs\n"
        f"Entry Guidance: {entry_guidance}\n"
        f"{history_note}\n\n"
        f"Score: {info['away_runs']}-{info['home_runs']}\n"
        f"Inning: {info['inning_state']} {info['inning']}\n"
        f"Base/Out: {info['base_state']['label']}, {info['outs']} out(s)\n\n"
        f"Scores:\n"
        f"CIP: {scores['current_inning_pressure']}/100\n"
        f"RO: {scores['remaining_opportunity']}/100\n"
        f"Stress: {scores['pitcher_stress']}/100\n"
        f"Dom: {scores['dominance']}/100\n"
        f"Contact: {scores['contact_quality']}/100\n"
        f"Contact Trend: {scores.get('contact_trend', 0)}/100\n"
        f"Lineup: {scores['lineup_pressure']}/100\n"
        f"Bullpen Risk: {scores['bullpen_risk']}/100\n"
        f"Starter Exit: {scores.get('starter_exit_probability', 0)}/100\n"
        f"Times Through Order: {scores.get('times_through_order', 0)}/100\n"
        f"Fake Pressure: {scores.get('fake_pressure', 0)}/100\n"
        f"Under Environment: {scores.get('under_environment', 0)}/100\n"
        f"Conv: {scores.get('run_conversion', 0)}/100\n"
        f"P2R: {scores.get('pressure_to_runs', 0)}/100\n"
        f"PreOver: {scores.get('pre_run_over_watch', 0)}/100\n"
        f"OVal: {scores.get('over_value', 0)}/100\n"
        f"Prev: {scores.get('run_prevention', 0)}/100\n"
        f"PredMove: {scores.get('predictive_market_move', 0)}/100\n"
        f"Threat: {scores.get('threat_index', 0)}/100\n"
        f"Stack: {scores.get('signal_stack', 0)} ({', '.join(scores.get('signal_stack_labels', []))})\n"
        f"Lag: {scores.get('market_lag', 0)}/100\n"
        f"ConvAccel: {scores.get('conv_acceleration', 0)}/100\n"
        f"KEnv: {scores.get('strikeout_environment', 0)}/100\n"
        f"BPLock: {scores.get('bullpen_lockdown', 0)}/100\n"
        f"TConv: {scores.get('traffic_conversion', 0)}/100\n"
        f"HHEff: {scores.get('hard_hit_efficiency', 0)}/100\n"
        f"HHUnder: {scores.get('hard_hit_under_support', 0)}/100\n"
        f"Expires If: {expiration_text(info, opportunity)}\n"
        f"P2RBoost: {scores.get('p2r_boost', 0)}\n"
        f"Market Resistance: {scores.get('market_resistance', 0)}\n"
        f"Run Suppression: {scores['run_suppression']}/100\n"
        f"False Dominance: {scores['false_dominance']}/100\n"
        f"Live Evidence: {opportunity.get('evidence', {}).get('real_signal_count', 0)} signal(s) - {opportunity.get('evidence', {}).get('reason', 'unknown')}\n\n"
        f"Why:\n"
        f"{reason_text}\n\n"
        f"Pitcher:\n"
        f"{info['pitcher_name']} ({info.get('pitcher_hand') or '?'})\n"
        f"Pitch Count: {p['pitch_count']}\n"
        f"Hits/Walks/K: {p['hits']}/{p['walks']}/{p['strikeouts']}\n"
        f"Batters Faced: {p['batters_faced']}\n\n"
        f"Pitch Quality:\n"
        f"Strike% {q['strike_pct']} | Whiff% {q['whiff_pct']} | Zone% {q['zone_pct']} | CSW% {q['csw_pct']}\n"
        f"VeloDrop {q['velo_drop']} | SpinDrop {q['spin_drop']} | MoveDrop {q['movement_drop']} | ReleaseDrift {q['release_drift']}\n"
        f"Avg EV {q['avg_ev']} | Recent EV {q.get('recent_avg_ev', 0)} | EV Trend {q.get('ev_trend', 0)} | Max EV {q['max_ev']} | HH {q['hard_hit']} | Barrels {q['barrels']}\n\n"
        f"Current/Upcoming Hitters:\n"
        f"{format_hitters(hitters)}\n\n"
        f"Pitch-Type Flags:\n"
        f"{flag_text}"
    )


def determine_next_sleep(any_live, any_near_strike):
    if any_near_strike:
        return FAST_POLL_SECONDS
    if any_live:
        return ACTIVE_POLL_SECONDS
    return SLOW_POLL_SECONDS



def current_recommendations_snapshot(state):
    """
    Returns the current login/feed recommendation for every tracked game.
    This makes the stored current_recommendation usable by a dashboard, log viewer, or future endpoint.
    """
    rows = []
    for key, game_state in (state or {}).get("games", {}).items():
        rec = game_state.get("current_recommendation")
        if not rec:
            continue
        item = dict(rec)
        item["state_key"] = key
        thesis = game_state.get("active_thesis")
        if thesis:
            item["active_thesis"] = thesis_summary_text(thesis)
        rows.append(item)
    rows.sort(key=lambda r: r.get("updated_at", ""), reverse=True)
    return rows


def mask_secret(value, keep=3):
    """Never print full secrets in Railway logs."""
    value = str(value or "")
    if not value:
        return "MISSING"
    if len(value) <= keep:
        return "***"
    return value[:keep] + "***"


def print_startup_banner():
    """Print one unmistakable startup banner so Railway logs prove the active code version."""
    print("\n" + "=" * 72)
    print(f"🚀 {APP_BUILD_LABEL}")
    print(f"DEPLOY MARKER: {DEPLOY_MARKER}")
    print(f"PYTHON FILE: {os.path.abspath(__file__)}")
    print(f"WORKING DIR: {os.getcwd()}")
    print(f"STATE FILE: {STATE_FILE}")
    print("-" * 72)
    print("EMAIL CONFIG:")
    print(f"  ENABLE_NIGHTLY_EMAIL_REPORT={ENABLE_NIGHTLY_EMAIL_REPORT}")
    print(f"  NIGHTLY_EMAIL_TO={NIGHTLY_EMAIL_TO or 'MISSING'}")
    print(f"  EMAIL_FROM={EMAIL_FROM or 'MISSING'}")
    print(f"  SMTP_HOST={SMTP_HOST or 'MISSING'}")
    print(f"  SMTP_PORT={SMTP_PORT}")
    print(f"  SMTP_USER={SMTP_USER or 'MISSING'}")
    print(f"  SMTP_PASSWORD={mask_secret(SMTP_PASSWORD)}")
    print(f"  SMTP_USE_TLS={SMTP_USE_TLS}")
    print(f"  ATTACH_DAILY_CSVS_TO_EMAIL={ATTACH_DAILY_CSVS_TO_EMAIL}")
    print(f"  DAILY_LEARNING_REPORT_HOUR={DAILY_LEARNING_REPORT_HOUR} AZ")
    print("-" * 72)
    print("BOOK CONFIG:")
    print(f"  USER_PLAYABLE_BOOKS={USER_PLAYABLE_BOOKS}")
    print(f"  MARKET_REFERENCE_BOOKS={MARKET_REFERENCE_BOOKS}")
    print(f"  IGNORE_RECOMMENDATION_BOOKS={IGNORE_RECOMMENDATION_BOOKS}")
    print(f"  REQUIRE_PLAYABLE_BOOK_FOR_STRIKE={REQUIRE_PLAYABLE_BOOK_FOR_STRIKE}")
    print("=" * 72 + "\n")

def main():
    print_startup_banner()
    state = load_state()
    build_learning_summary()
    print("REAL-TIME DATA ADAPTER:", realtime_data_status())

    if RUN_EMAIL_TEST_ON_START:
        print("RUN_EMAIL_TEST_ON_START=true | sending one startup nightly email test now.")
        send_nightly_summary_email(today())

    while True:
        any_live = False
        any_near_strike = False

        try:
            games = get_schedule()

            # Credit-smart odds usage:
            # Only call the Odds API when at least one game is inside the pregame window
            # or already active. If all games are too far away, stay dormant and skip odds.
            needs_odds = False
            for sg in games:
                sg_pk = str(sg["gamePk"])
                if is_final_locked_today(state, sg_pk):
                    continue

                sg_status = schedule_status(sg)
                if is_final_status(sg_status):
                    sg_label = schedule_label(sg)
                    sg_score = schedule_final_score(sg)
                    mark_final_locked_today(
                        state,
                        sg_pk,
                        sg_label,
                        sg_status,
                        sg_score,
                    )
                    if sg_score:
                        grade_completed_strikes(sg_pk, sg_label, sg_score)
                    continue

                st = parse_start_time(sg)
                if st is None or should_fetch_feed(st):
                    needs_odds = True
                    break

            odds = get_odds() if needs_odds else []
            if not needs_odds:
                print("ODDS SKIPPED: all games outside pregame window.")

            print(f"\n--- {APP_BUILD_LABEL} CHECK {now_local().strftime('%I:%M:%S %p')} ---")

            for g in games:
                game_pk = str(g["gamePk"])
                start_time = parse_start_time(g)

                if is_final_locked_today(state, game_pk):
                    print(f"SKIP FINAL | {schedule_label(g)} | already final-locked for {today()}")
                    continue

                g_status = schedule_status(g)
                if is_final_status(g_status):
                    g_label = schedule_label(g)
                    g_score = schedule_final_score(g)
                    mark_final_locked_today(
                        state,
                        game_pk,
                        g_label,
                        g_status,
                        g_score,
                    )
                    if g_score:
                        grade_completed_strikes(game_pk, g_label, g_score)
                    print(f"FINAL LOCKED | {g_label} | Status {g_status} | Score {g_score or 'Unknown'} | no more tracking today")
                    save_state(state)
                    continue

                if game_pk not in state["games"]:
                    state["games"][game_pk] = {
                        "opening_total": None,
                        "alerts": [],
                        "active_thesis": None,
                        "current_recommendation": None,
                    }

                state_game = state["games"][game_pk]

                if start_time and not should_fetch_feed(start_time):
                    home = g.get("teams", {}).get("home", {}).get("team", {}).get("name", "Home")
                    away = g.get("teams", {}).get("away", {}).get("team", {}).get("name", "Away")
                    print(f"DORMANT | {away} at {home} | Start {start_time.strftime('%I:%M %p')} AZ | Too early")
                    continue

                feed = get_feed(game_pk)
                info = parse_game(feed, g)

                label = f"{info['away']} at {info['home']}"
                start_label = info["start_time"].strftime("%I:%M %p AZ") if info["start_time"] else "Unknown"
                mode = "ACTIVE" if info["status"] == "Live" else "FINAL" if is_final_status(info["status"]) else "DORMANT"

                if is_final_status(info["status"]):
                    final_score = f"{info['away_runs']}-{info['home_runs']}"
                    mark_final_locked_today(
                        state,
                        game_pk,
                        label,
                        info["status"],
                        final_score,
                    )
                    grade_completed_strikes(game_pk, label, final_score)
                    print(f"FINAL LOCKED | {label} | Score {final_score} | no more tracking today")
                    save_state(state)
                    continue

                markets = find_markets(odds, info["home"], info["away"])
                live_total = markets["total"]["point"]
                velocity_info = update_line_velocity_state(state_game, markets)
                update_active_clv_snapshots(info, live_total)

                # V3.2: this is the first total this bot saw today, not necessarily the true opener.
                if state_game["opening_total"] is None and live_total:
                    state_game["opening_total"] = live_total
                state_game.setdefault("first_seen_total", state_game.get("opening_total"))
                state_game.setdefault("true_opening_total", None)

                opening_total = state_game["opening_total"]
                first_seen_total = state_game.get("first_seen_total")
                true_opening_total = state_game.get("true_opening_total")

                p = pitcher_box(feed, info["pitcher_id"])
                q = live_statcast_quality(feed, info["pitcher_id"])
                traffic = traffic_metrics(feed, info["pitcher_id"])
                hitters = upcoming_hitters(feed, info, 4)

                lineup_pressure = lineup_pressure_score(info, hitters)
                stress = pitcher_stress_score(p, q, traffic)
                dominance = pitcher_dominance_score(p, q, traffic)
                contact = contact_quality_score(q, traffic)
                pitching_context = game_pitching_context(feed, info)
                strikeout_env = strikeout_environment_score(info, p, q, traffic, pitching_context)
                bullpen_lockdown = bullpen_lockdown_score(info, p, q, pitching_context)
                traffic_conversion_pressure = traffic_conversion_score(info, p, q, traffic)
                hard_hit_efficiency = hard_hit_efficiency_score(info, p, q, traffic)
                hard_hit_under_support = hard_hit_under_support_score(info, p, q, traffic)
                bullpen = bullpen_risk_score(info, p)
                remaining_opp = remaining_opportunity_score(info, lineup_pressure, bullpen)
                current_pressure = current_inning_pressure_score(info, lineup_pressure, stress, contact)
                suppression = run_suppression_score(info, p, q, traffic, contact)
                false_dom = false_dominance_score(dominance, stress, contact, traffic)

                contact_trend = contact_trend_score(q)
                tto = times_through_order_score(info, p, hitters)
                starter_exit = starter_exit_probability(info, p, stress, bullpen)
                fake_pressure = fake_pressure_score(info, p, q, traffic, dominance, contact, current_pressure)
                market_res = market_resistance_score(opening_total, live_total, info, current_pressure, contact, dominance)
                blowout = blowout_kill_score(info)
                under_env = under_environment_score(info, p, q, traffic, dominance, contact, current_pressure, remaining_opp, fake_pressure, blowout)
                run_conversion = run_conversion_score(info, p, q, traffic, hitters, current_pressure, remaining_opp, stress, contact, lineup_pressure, contact_trend, tto, starter_exit, fake_pressure, traffic_conversion_pressure, hard_hit_efficiency, strikeout_env, bullpen_lockdown)
                run_prevention = run_prevention_score(info, p, q, traffic, dominance, contact, current_pressure, remaining_opp, fake_pressure, under_env, blowout, strikeout_env, bullpen_lockdown, hard_hit_under_support, traffic_conversion_pressure, hard_hit_efficiency)

                # Temporary score dict for over predictive calculation.
                # IMPORTANT: initialize these BEFORE using them in dictionaries.
                pressure_to_runs = 0
                pre_run_over = 0
                over_value = 0
                predictive_move = 0

                base_scores_for_over = {
                    "current_inning_pressure": current_pressure,
                    "remaining_opportunity": remaining_opp,
                    "pitcher_stress": stress,
                    "dominance": dominance,
                    "contact_quality": contact,
                    "contact_trend": contact_trend,
                    "lineup_pressure": lineup_pressure,
                    "bullpen_risk": bullpen,
                    "starter_exit_probability": starter_exit,
                    "times_through_order": tto,
                    "fake_pressure": fake_pressure,
                    "market_resistance": market_res,
                    "blowout_kill": blowout,
                    "under_environment": under_env,
                    "run_conversion": run_conversion,
                    "run_prevention": run_prevention,
                    "pressure_to_runs": 0,
                    "pre_run_over_watch": 0,
                    "over_value": 0,
                    "predictive_market_move": 0,
                    "strikeout_environment": strikeout_env,
                    "bullpen_lockdown": bullpen_lockdown,
                    "traffic_conversion": traffic_conversion_pressure,
                    "hard_hit_efficiency": hard_hit_efficiency,
                    "hard_hit_under_support": hard_hit_under_support,
                }
                pressure_to_runs = pressure_to_runs_score(info, p, q, traffic, hitters, base_scores_for_over)
                base_scores_for_over["pressure_to_runs"] = pressure_to_runs
                pre_run_over = pre_run_over_watch_score(opening_total, live_total, info, p, q, traffic, base_scores_for_over)
                base_scores_for_over["pre_run_over_watch"] = pre_run_over

                # Temporary score dict for predictive market-move calculation.
                pre_scores = {
                    "current_inning_pressure": current_pressure,
                    "remaining_opportunity": remaining_opp,
                    "pitcher_stress": stress,
                    "dominance": dominance,
                    "contact_quality": contact,
                    "contact_trend": contact_trend,
                    "lineup_pressure": lineup_pressure,
                    "bullpen_risk": bullpen,
                    "starter_exit_probability": starter_exit,
                    "times_through_order": tto,
                    "fake_pressure": fake_pressure,
                    "market_resistance": market_res,
                    "blowout_kill": blowout,
                    "under_environment": under_env,
                    "run_conversion": run_conversion,
                    "run_prevention": run_prevention,
                    "pressure_to_runs": pressure_to_runs,
                    "pre_run_over_watch": pre_run_over,
                    "over_value": over_value,
                    "strikeout_environment": strikeout_env,
                    "bullpen_lockdown": bullpen_lockdown,
                    "traffic_conversion": traffic_conversion_pressure,
                    "hard_hit_efficiency": hard_hit_efficiency,
                    "hard_hit_under_support": hard_hit_under_support,
                }
                predictive_move = predictive_market_move_score(opening_total, live_total, info, p, q, traffic, pre_scores)
                pre_scores["predictive_market_move"] = predictive_move
                provisional_edge = 0
                if live_total is not None:
                    # Use current score vs live total as a conservative placeholder before full projection.
                    # Full edge is calculated later by detect_total_opportunity after expected future runs.
                    provisional_edge = info.get("total_runs", 0) - live_total
                over_value = over_value_score(opening_total, live_total, provisional_edge, pre_scores)
                pre_scores["over_value"] = over_value
                pre_scores["pre_run_over_watch"] = pre_run_over
                pre_scores["pressure_to_runs"] = pressure_to_runs
                pre_scores["predictive_market_move"] = predictive_move

                expected_future = expected_future_runs(
                    info,
                    current_pressure,
                    remaining_opp,
                    stress,
                    dominance,
                    contact,
                    bullpen,
                    lineup_pressure,
                    suppression,
                    false_dom,
                    contact_trend,
                    tto,
                    starter_exit,
                    fake_pressure,
                    market_res,
                    blowout,
                    under_env,
                    run_conversion,
                    run_prevention,
                    predictive_move,
                    pressure_to_runs,
                    pre_run_over,
                    strikeout_env,
                    bullpen_lockdown,
                    traffic_conversion_pressure,
                    hard_hit_efficiency,
                    hard_hit_under_support,
                )

                projected_total = projected_final_total(info, expected_future)

                scenario = classify_scenario(
                    info,
                    opening_total,
                    live_total,
                    current_pressure,
                    remaining_opp,
                    stress,
                    dominance,
                    contact,
                    bullpen,
                    lineup_pressure,
                    suppression,
                    false_dom,
                    contact_trend,
                    tto,
                    starter_exit,
                    fake_pressure,
                    market_res,
                    blowout,
                    under_env,
                    strikeout_env,
                    bullpen_lockdown,
                    traffic_conversion_pressure,
                    hard_hit_efficiency,
                    hard_hit_under_support,
                )

                scores = {
                    "current_inning_pressure": current_pressure,
                    "remaining_opportunity": remaining_opp,
                    "pitcher_stress": stress,
                    "dominance": dominance,
                    "contact_quality": contact,
                    "contact_trend": contact_trend,
                    "lineup_pressure": lineup_pressure,
                    "bullpen_risk": bullpen,
                    "starter_exit_probability": starter_exit,
                    "times_through_order": tto,
                    "fake_pressure": fake_pressure,
                    "market_resistance": market_res,
                    "blowout_kill": blowout,
                    "under_environment": under_env,
                    "run_conversion": run_conversion,
                    "run_prevention": run_prevention,
                    "pressure_to_runs": pressure_to_runs,
                    "pre_run_over_watch": pre_run_over,
                    "over_value": over_value,
                    "predictive_market_move": predictive_move,
                    "run_suppression": suppression,
                    "false_dominance": false_dom,
                    "strikeout_environment": strikeout_env,
                    "bullpen_lockdown": bullpen_lockdown,
                    "traffic_conversion": traffic_conversion_pressure,
                    "hard_hit_efficiency": hard_hit_efficiency,
                    "hard_hit_under_support": hard_hit_under_support,
                    "opening_total": opening_total,
                    "first_seen_total": first_seen_total,
                    "true_opening_total": true_opening_total,
                    "live_total": live_total,
                }
                scores = apply_market_reaction_scores(info, opening_total, first_seen_total, live_total, scores)

                if info["status"] == "Live":
                    any_live = True

                opportunity = detect_total_opportunity(
                    markets["total"],
                    info,
                    projected_total,
                    scenario,
                    scores,
                    p,
                    q,
                    traffic,
                    state,
                )

                # Market intelligence uses book-level totals when the Odds API returns them.
                side_for_market = opportunity.get("side") if opportunity else None
                market_snapshot_info = dict(markets.get("market", {}))
                best_for_side = best_available_for_side(markets.get("book_totals", []), side_for_market) if side_for_market else None
                price_adjusted_best = price_adjusted_best_available_for_side(markets.get("book_totals", []), side_for_market) if side_for_market else None
                if price_adjusted_best:
                    market_snapshot_info["best_for_side"] = price_adjusted_best
                elif best_for_side:
                    market_snapshot_info["best_for_side"] = best_for_side
                market_conf = market_confirmation_score(side_for_market, market_snapshot_info, velocity_info, opening_total, live_total) if side_for_market else 50
                best_line_for_age = price_adjusted_best or best_for_side or {}
                best_line_age = book_line_age_seconds(best_line_for_age)
                market_context_for_state = {
                    "opening_total": opening_total,
                    "first_seen_total": first_seen_total,
                    "true_opening_total": true_opening_total,
                    "live_total": live_total,
                    "primary_book": markets.get("total", {}).get("book"),
                    "book_count": market_snapshot_info.get("book_count", 0),
                    "consensus_total": market_snapshot_info.get("consensus_total"),
                    "market_min_total": market_snapshot_info.get("market_min_total"),
                    "market_max_total": market_snapshot_info.get("market_max_total"),
                    "market_disagreement": market_snapshot_info.get("market_disagreement"),
                    "line_velocity": velocity_info.get("line_velocity"),
                    "line_direction": velocity_info.get("line_direction"),
                    "book_velocity_summary": velocity_info.get("book_velocity_summary"),
                    "leading_book": velocity_info.get("leading_book"),
                    "best_book": (best_for_side or {}).get("book"),
                    "best_available_total": (best_for_side or {}).get("point"),
                    "best_available_price": (best_for_side or {}).get("side_price"),
                    "price_adjusted_best_book": (price_adjusted_best or {}).get("book"),
                    "price_adjusted_best_total": (price_adjusted_best or {}).get("point"),
                    "price_adjusted_best_price": (price_adjusted_best or {}).get("side_price"),
                    "best_line_last_update": (best_line_for_age or {}).get("last_update"),
                    "best_line_age_seconds": best_line_age,
                    "market_confirmation_score": market_conf,
                    "market_discount": market_discount_value(first_seen_total, live_total),
                    "market_discount_score": market_discount_score(side_for_market, first_seen_total, live_total, scores),
                }
                alert_opportunity, decision_reason = apply_professional_decision_layer(
                    state_game,
                    info,
                    market_context_for_state,
                    opportunity,
                )

                edge_for_sleep = abs(opportunity["edge"]) if opportunity else 0
                if edge_for_sleep >= 0.7:
                    any_near_strike = True

                flags = pitch_type_red_flags(q)

                print(
                    f"{mode} | {label} | {info['inning_state']} {info['inning']} | "
                    f"Score {info['away_runs']}-{info['home_runs']} | Base {info['base_state']['label']} {info['outs']} out | "
                    f"Open {opening_total} Live {live_total} Projected {projected_total} EFR {expected_future} | "
                    f"Books {market_context_for_state.get('book_count', 0)} Cons {market_context_for_state.get('consensus_total')} BestPlayable {market_context_for_state.get('best_book')} {market_context_for_state.get('best_available_total')} RecPlayable {market_context_for_state.get('price_adjusted_best_book')} {market_context_for_state.get('price_adjusted_best_total')} Vel {market_context_for_state.get('line_velocity')} MktConf {market_context_for_state.get('market_confirmation_score')} | "
                    f"Scenario {scenario} | Reaction {scores.get('market_reaction_profile')} Move {scores.get('market_reaction_move')} Settle {scores.get('settle_down_score')} Cont {scores.get('continuation_score')} FalseInfl {scores.get('false_inflation_score')} | "
                    f"CIP {current_pressure} RO {remaining_opp} Stress {stress} Dom {dominance} Contact {contact} "
                    f"Trend {contact_trend} Lineup {lineup_pressure} Bullpen {bullpen} Exit {starter_exit} TTO {tto} "
                    f"Fake {fake_pressure} UnderEnv {under_env} Conv {run_conversion} Prev {run_prevention} KEnv {strikeout_env} BPLock {bullpen_lockdown} TConv {traffic_conversion_pressure} HHEff {hard_hit_efficiency} HHUnder {hard_hit_under_support} PredMove {predictive_move} MarketRes {market_res} Supp {suppression} FalseDom {false_dom} | "
                    f"Pitcher {info['pitcher_name']} PC {p['pitch_count']} H/W/K {p['hits']}/{p['walks']}/{p['strikeouts']} | "
                    f"Next {format_hitters(hitters)}"
                )

                if info["status"] != "Live":
                    save_state(state)
                    continue

                if alert_opportunity and should_alert(state_game, alert_opportunity):
                    market_context = market_context_for_state
                    msg = format_alert(
                        label,
                        start_label,
                        info,
                        market_context,
                        alert_opportunity,
                        p,
                        q,
                        traffic,
                        hitters,
                        flags,
                    )
                    sms_body = format_bet_now_sms(label, info, market_context, alert_opportunity)
                    send_text(msg, sms_body=sms_body)
                    log_strike_history(
                        info,
                        alert_opportunity,
                        {
                            **market_context,
                        },
                    )
                    record_active_thesis(state_game, info, alert_opportunity)

                save_state(state)

        except Exception as e:
            print("ERROR:", repr(e))

        maybe_send_daily_learning_report(state, any_live)

        sleep_seconds = determine_next_sleep(any_live, any_near_strike)
        print(f"Sleeping {sleep_seconds} seconds...\n")
        time.sleep(sleep_seconds)


# ---------------------------------------------------------------------------
# V3.10.0 Rolling Dashboards + Feature Learning Overrides
# ---------------------------------------------------------------------------
# These overrides intentionally sit after the V3.9 functions so Python uses the
# upgraded versions at runtime without disturbing the core live-betting engine.
# Goals:
#   1) keep the master decision database practical and durable;
#   2) preserve near-closing line snapshots inside the decision log;
#   3) add 7-day / 30-day / season dashboards;
#   4) add controlled feature-level learning on top of profile-level learning;
#   5) cap adaptive adjustments so the model cannot overreact to noise.

FEATURE_LEARNING_FILE = os.getenv("FEATURE_LEARNING_FILE", "feature_learning_summary.csv")
ENABLE_FEATURE_LEARNING = os.getenv("ENABLE_FEATURE_LEARNING", "true").lower() == "true"
ENABLE_ROLLING_DECISION_DASHBOARD = os.getenv("ENABLE_ROLLING_DECISION_DASHBOARD", "true").lower() == "true"
MIN_FEATURE_SAMPLE = int(os.getenv("MIN_FEATURE_SAMPLE", "50"))
FEATURE_STRONG_ROI = float(os.getenv("FEATURE_STRONG_ROI", "0.035"))
FEATURE_WEAK_ROI = float(os.getenv("FEATURE_WEAK_ROI", "-0.025"))
FEATURE_STRONG_CLV = float(os.getenv("FEATURE_STRONG_CLV", "0.20"))
FEATURE_WEAK_CLV = float(os.getenv("FEATURE_WEAK_CLV", "-0.20"))
FEATURE_PROVEN_CONF_BONUS = int(os.getenv("FEATURE_PROVEN_CONF_BONUS", "2"))
FEATURE_TIGHTEN_CONF_PENALTY = int(os.getenv("FEATURE_TIGHTEN_CONF_PENALTY", "3"))
MAX_TOTAL_ADAPTIVE_CONF_ADJ = int(os.getenv("MAX_TOTAL_ADAPTIVE_CONF_ADJ", "8"))
MIN_CLV_SAMPLE_FOR_ADAPTIVE = int(os.getenv("MIN_CLV_SAMPLE_FOR_ADAPTIVE", "25"))
DECISION_SEASON_START_DATE = os.getenv("DECISION_SEASON_START_DATE", "2026-03-01")


def _parse_iso_date_safe(value):
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except Exception:
        return None


def _row_date(row):
    return _parse_iso_date_safe((row or {}).get("date"))


def _days_back_rows(rows, report_date, days):
    end = _parse_iso_date_safe(report_date or today())
    if not end:
        return rows or []
    start_ord = end.toordinal() - int(days) + 1
    out = []
    for r in rows or []:
        d = _row_date(r)
        if d and start_ord <= d.toordinal() <= end.toordinal():
            out.append(r)
    return out


def _season_rows(rows):
    start = _parse_iso_date_safe(DECISION_SEASON_START_DATE)
    if not start:
        return rows or []
    return [r for r in (rows or []) if _row_date(r) and _row_date(r) >= start]


def _graded_decision_rows(actions=None):
    actions = set(actions or ["BET_NOW", "TEST_UNIT"])
    return [
        r for r in csv_read_rows(DECISION_LOG_FILE)
        if r.get("result") in ["WIN", "LOSS", "PUSH"] and r.get("action") in actions
    ]


def _summarize_units_and_clv(rows):
    rows = rows or []
    w, l, p, pct, units = summarize_record(rows)
    clvs = [safe_float(r.get("clv"), None) for r in rows if safe_float(r.get("clv"), None) is not None]
    avg_clv = round(avg(clvs), 2) if clvs else ""
    clv_sample = len(clvs)
    return w, l, p, pct, units, avg_clv, clv_sample


def decision_log_fieldnames():
    # Superset of V3.9 fields. csv.DictWriter extrasaction="ignore" means older rows remain safe.
    return [
        "decision_id", "timestamp", "date", "game_key", "game_pk", "game",
        "action", "decision_type", "reject_reason",
        "profile", "scenario", "side", "line", "price", "book",
        "opening_total", "first_seen_total", "true_opening_total", "live_total",
        "projected_total", "edge", "confidence", "expected_value",
        "inning", "inning_state", "outs", "score", "base_out",
        "projection_score", "confirmation_score", "market_confirmation_score",
        "market_value_score", "risk_filter_score", "calculated_risk_tier", "suggested_unit",
        "pressure_to_runs", "run_conversion", "traffic_conversion", "pitcher_stress",
        "contact_quality", "bullpen_risk", "run_prevention", "strikeout_environment",
        "bullpen_lockdown", "settle_down_score", "continuation_score",
        "discounted_over_score", "false_inflation_score", "continuation_exhaustion_score",
        "pitching_dominance_under_score", "market_reaction_move", "market_discount",
        "consensus_total", "market_min_total", "market_max_total", "market_disagreement",
        "line_velocity", "line_direction", "book_count", "recommended_book",
        "recommended_total", "recommended_price", "best_line_age_seconds",
        "adaptive_status", "adaptive_confidence_adjustment", "adaptive_profile_adjustment",
        "adaptive_feature_adjustment", "adaptive_sample", "adaptive_roi", "adaptive_avg_clv",
        "pattern_tags", "bet_quality", "quality_reason",
        "last_live_total", "last_clv_snapshot_at", "last_clv_snapshot_type", "closing_total_estimate",
        "final_score", "final_total", "result", "units", "clv", "graded_at",
    ]


def feature_learning_fieldnames():
    return [
        "feature_key", "sample", "wins", "losses", "pushes", "win_pct", "units",
        "roi", "avg_clv", "clv_sample", "status", "confidence_adjustment", "updated_at"
    ]


def feature_keys_from_decision_row(row):
    row = row or {}
    keys = []
    profile = row.get("profile") or "UNCLASSIFIED"
    side = str(row.get("side") or "").upper()
    action = row.get("action") or ""
    if profile:
        keys.append(f"PROFILE:{profile}")
    if side:
        keys.append(f"SIDE:{side}")
        keys.append(f"PROFILE_SIDE:{profile}:{side}")
    tier = row.get("calculated_risk_tier") or ""
    if tier:
        keys.append(f"TIER:{tier}")
    inning = safe_int(row.get("inning"), 0)
    if inning:
        if inning <= 3:
            keys.append("INNING:EARLY_1_3")
        elif inning <= 6:
            keys.append("INNING:MIDDLE_4_6")
        else:
            keys.append("INNING:LATE_7_PLUS")
    price = safe_int(row.get("price"), 0)
    if price:
        if price <= -130:
            keys.append("PRICE:EXPENSIVE_FAVORITE")
        elif -129 <= price <= -105:
            keys.append("PRICE:STANDARD")
        elif price >= 100:
            keys.append("PRICE:PLUS_MONEY")
    try:
        tags = [t for t in str(row.get("pattern_tags") or "").split("|") if t]
    except Exception:
        tags = []
    for t in tags[:8]:
        keys.append(f"TAG:{t}")
    for metric, threshold in [
        ("pressure_to_runs", 85), ("run_conversion", 80), ("traffic_conversion", 75),
        ("pitcher_stress", 80), ("contact_quality", 75), ("bullpen_risk", 70),
        ("settle_down_score", 70), ("continuation_score", 80), ("discounted_over_score", 70),
        ("pitching_dominance_under_score", 70),
    ]:
        if safe_int(row.get(metric), 0) >= threshold:
            keys.append(f"METRIC:{metric.upper()}_{threshold}_PLUS")
    if action:
        keys.append(f"ACTION:{action}")
    # Dedupe while preserving order.
    seen = set()
    clean = []
    for k in keys:
        if k and k not in seen:
            seen.add(k)
            clean.append(k)
    return clean


def build_feature_learning_summary():
    if not ENABLE_FEATURE_LEARNING:
        return []
    rows = _graded_decision_rows(actions=["BET_NOW", "TEST_UNIT"])
    buckets = {}
    for r in rows:
        for key in feature_keys_from_decision_row(r):
            buckets.setdefault(key, []).append(r)

    out = []
    now_iso = now_local().isoformat()
    for key, bucket in sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True):
        w, l, p, pct, units, avg_clv, clv_sample = _summarize_units_and_clv(bucket)
        sample = len(bucket)
        roi = round(units / max(1, len([x for x in bucket if x.get("result") in ["WIN", "LOSS"]])), 4)
        avg_clv_num = safe_float(avg_clv, 0) if avg_clv != "" else 0
        if sample < MIN_FEATURE_SAMPLE:
            status = "OPEN_TEST"
            adj = 0
        elif roi >= FEATURE_STRONG_ROI and clv_sample >= MIN_CLV_SAMPLE_FOR_ADAPTIVE and avg_clv_num >= FEATURE_STRONG_CLV:
            status = "PROVEN"
            adj = FEATURE_PROVEN_CONF_BONUS
        elif roi <= FEATURE_WEAK_ROI or (clv_sample >= MIN_CLV_SAMPLE_FOR_ADAPTIVE and avg_clv_num <= FEATURE_WEAK_CLV):
            status = "TIGHTEN"
            adj = -FEATURE_TIGHTEN_CONF_PENALTY
        else:
            status = "HOLD"
            adj = 0
        out.append({
            "feature_key": key,
            "sample": sample,
            "wins": w,
            "losses": l,
            "pushes": p,
            "win_pct": pct,
            "units": units,
            "roi": roi,
            "avg_clv": avg_clv,
            "clv_sample": clv_sample,
            "status": status,
            "confidence_adjustment": adj,
            "updated_at": now_iso,
        })
    csv_write_rows(FEATURE_LEARNING_FILE, feature_learning_fieldnames(), out)
    return out


def build_adaptive_config_from_results():
    """
    V3.10 profile config: requires mature sample and enough CLV observations before
    applying positive upgrades. Negative protection can trigger on ROI even when CLV is sparse.
    """
    if not ENABLE_ADAPTIVE_CONFIG:
        return {}
    rows = _graded_decision_rows(actions=["BET_NOW", "TEST_UNIT"])
    profiles = {}
    for r in rows:
        profile = r.get("profile") or r.get("market_reaction_profile") or "UNCLASSIFIED"
        profiles.setdefault(profile, []).append(r)

    config = {}
    for profile, bucket in profiles.items():
        w, l, p, pct, units, avg_clv, clv_sample = _summarize_units_and_clv(bucket)
        sample = len(bucket)
        risked = max(1, len([x for x in bucket if x.get("result") in ["WIN", "LOSS"]]))
        roi = round(units / risked, 4)
        avg_clv_num = safe_float(avg_clv, 0) if avg_clv != "" else 0
        if sample < MIN_ADAPTIVE_SAMPLE:
            status = "OPEN_TEST"
            conf_adj = 0
            tier_bias = "none"
        elif roi >= ADAPTIVE_STRONG_ROI and clv_sample >= MIN_CLV_SAMPLE_FOR_ADAPTIVE and avg_clv_num >= ADAPTIVE_STRONG_CLV:
            status = "PROVEN"
            conf_adj = min(ADAPTIVE_PROVEN_CONF_BONUS, MAX_TOTAL_ADAPTIVE_CONF_ADJ)
            tier_bias = "upgrade"
        elif roi <= ADAPTIVE_WEAK_ROI and clv_sample >= MIN_CLV_SAMPLE_FOR_ADAPTIVE and avg_clv_num <= ADAPTIVE_WEAK_CLV:
            status = "FAILING"
            conf_adj = -min(ADAPTIVE_FAILING_CONF_PENALTY, MAX_TOTAL_ADAPTIVE_CONF_ADJ)
            tier_bias = "downgrade"
        elif roi <= ADAPTIVE_WEAK_ROI or (clv_sample >= MIN_CLV_SAMPLE_FOR_ADAPTIVE and avg_clv_num <= ADAPTIVE_WEAK_CLV):
            status = "TIGHTEN"
            conf_adj = -min(ADAPTIVE_TIGHTEN_CONF_PENALTY, MAX_TOTAL_ADAPTIVE_CONF_ADJ)
            tier_bias = "downgrade"
        else:
            status = "HOLD"
            conf_adj = 0
            tier_bias = "none"
        config[profile] = {
            "sample": sample,
            "wins": w,
            "losses": l,
            "pushes": p,
            "win_pct": pct,
            "units": units,
            "roi": roi,
            "avg_clv": avg_clv,
            "clv_sample": clv_sample,
            "status": status,
            "confidence_adjustment": conf_adj,
            "tier_bias": tier_bias,
            "updated_at": now_local().isoformat(),
        }
    save_adaptive_config(config)
    build_feature_learning_summary()
    return config


def _feature_adjustment_for_opportunity(opportunity):
    if not ENABLE_FEATURE_LEARNING or not opportunity:
        return 0, []
    rows = csv_read_rows(FEATURE_LEARNING_FILE)
    if not rows:
        rows = build_feature_learning_summary()
    by_key = {r.get("feature_key"): r for r in rows}
    pseudo_row = {
        "profile": market_reaction_profile_from_scores(opportunity.get("scores", {}) or {}, opportunity.get("scenario")),
        "side": opportunity.get("side"),
        "calculated_risk_tier": opportunity.get("calculated_risk_tier"),
        "price": opportunity.get("price") or opportunity.get("recommended_price"),
        "inning": opportunity.get("inning"),
        "pattern_tags": opportunity.get("pattern_tags", ""),
    }
    # Add score metrics for feature matching.
    for k, v in (opportunity.get("scores", {}) or {}).items():
        pseudo_row[k] = v
    matched = []
    total = 0
    for key in feature_keys_from_decision_row(pseudo_row):
        rec = by_key.get(key)
        if not rec:
            continue
        adj = safe_int(rec.get("confidence_adjustment"), 0)
        if adj:
            matched.append(f"{key}:{adj:+d}")
            total += adj
    # Feature learning should be a nudge, not a steering wheel.
    total = max(-4, min(4, total))
    return total, matched[:6]


def apply_adaptive_adjustment(opportunity):
    if not ENABLE_ADAPTIVE_CONFIG or not ENABLE_ADAPTIVE_CONFIDENCE or not opportunity:
        return opportunity
    opportunity = dict(opportunity)
    scores = opportunity.get("scores", {}) or {}
    profile = market_reaction_profile_from_scores(scores, opportunity.get("scenario"))
    config = load_adaptive_config()
    if not config:
        config = build_adaptive_config_from_results()
    profile_cfg = config.get(profile, {})
    profile_adj = safe_int(profile_cfg.get("confidence_adjustment"), 0)
    feature_adj, feature_matches = _feature_adjustment_for_opportunity(opportunity)
    total_adj = max(-MAX_TOTAL_ADAPTIVE_CONF_ADJ, min(MAX_TOTAL_ADAPTIVE_CONF_ADJ, profile_adj + feature_adj))

    original_conf = safe_int(opportunity.get("confidence"), 0)
    opportunity["confidence"] = clamp(original_conf + total_adj)
    opportunity["adaptive_status"] = profile_cfg.get("status", "OPEN_TEST" if profile else "")
    opportunity["adaptive_confidence_adjustment"] = total_adj
    opportunity["adaptive_profile_adjustment"] = profile_adj
    opportunity["adaptive_feature_adjustment"] = feature_adj
    opportunity["adaptive_feature_matches"] = "|".join(feature_matches)
    opportunity["adaptive_sample"] = profile_cfg.get("sample", 0)
    opportunity["adaptive_roi"] = profile_cfg.get("roi", "")
    opportunity["adaptive_avg_clv"] = profile_cfg.get("avg_clv", "")
    return opportunity


def update_decision_log_clv_snapshots(info, live_total):
    """
    V3.10: write the latest live total into every pending decision row for the same game.
    This creates a practical near-closing estimate because the final live poll before FINAL
    becomes the row's latest CLV snapshot. No extra API call is used.
    """
    if not ENABLE_DECISION_LOG or not ENABLE_CLV_TRACKING:
        return
    current_line = safe_float(live_total, None)
    if current_line is None:
        return
    rows = csv_read_rows(DECISION_LOG_FILE)
    if not rows:
        return
    info = info or {}
    changed = False
    for row in rows:
        if row.get("date") != today():
            continue
        if row.get("action") not in ["BET_NOW", "TEST_UNIT", "RESEARCH_ONLY", "NO_BET"]:
            continue
        if row.get("result") in ["WIN", "LOSS", "PUSH"]:
            continue
        same_game = row.get("game_pk") == str(info.get("game_pk")) or row.get("game") == f"{info.get('away')} at {info.get('home')}"
        if not same_game:
            continue
        side = str(row.get("side", "")).upper()
        alert_line = safe_float(row.get("line"), None)
        if side not in ["OVER", "UNDER"] or alert_line is None:
            continue
        clv = round(current_line - alert_line, 1) if side == "OVER" else round(alert_line - current_line, 1)
        old_clv = safe_float(row.get("clv"), None)
        # Always keep the latest line metadata, but only rewrite CLV if it changed enough or was blank.
        row["last_live_total"] = current_line
        row["last_clv_snapshot_at"] = now_local().isoformat()
        row["last_clv_snapshot_type"] = "poll_update"
        row["closing_total_estimate"] = current_line
        if old_clv is None or abs(clv - old_clv) >= CLV_SNAPSHOT_MIN_MOVE:
            row["clv"] = clv
        changed = True
    if changed:
        csv_write_rows(DECISION_LOG_FILE, decision_log_fieldnames(), rows)


def grade_completed_decision_log(game_pk, label, final_score):
    if not ENABLE_DECISION_LOG:
        return
    final_total = final_total_from_score(final_score)
    if final_total is None:
        return
    rows = csv_read_rows(DECISION_LOG_FILE)
    if not rows:
        return
    changed = False
    for row in rows:
        if str(row.get("game_pk")) != str(game_pk):
            continue
        if row.get("result") in ["WIN", "LOSS", "PUSH"]:
            continue
        side = str(row.get("side", "")).upper()
        line = safe_float(row.get("line"), None)
        if side not in ["OVER", "UNDER"] or line is None:
            continue
        result = grade_bet(side, line, final_total)
        row["final_score"] = final_score
        row["final_total"] = final_total
        row["result"] = result
        row["units"] = american_odds_profit_units(row.get("price"), result)
        # Preserve best available near-close CLV. If no poll snapshot ever occurred,
        # use last_live_total/closing_total_estimate as fallback before leaving blank.
        if row.get("clv") in [None, ""]:
            closing_est = safe_float(row.get("closing_total_estimate"), None)
            if closing_est is None:
                closing_est = safe_float(row.get("last_live_total"), None)
            if closing_est is not None:
                row["clv"] = round(closing_est - line, 1) if side == "OVER" else round(line - closing_est, 1)
        row["graded_at"] = now_local().isoformat()
        changed = True
    if changed:
        csv_write_rows(DECISION_LOG_FILE, decision_log_fieldnames(), rows)
        build_adaptive_config_from_results()


def _bucket_lines(title, rows, limit=8):
    lines = [title]
    if not rows:
        lines.append("• No graded rows yet.")
        return lines
    buckets = {}
    for r in rows:
        key = r.get("profile") or "UNCLASSIFIED"
        buckets.setdefault(key, []).append(r)
    for key, bucket in sorted(buckets.items(), key=lambda kv: abs(summarize_record(kv[1])[4]), reverse=True)[:limit]:
        w, l, p, pct, units, avg_clv, clv_sample = _summarize_units_and_clv(bucket)
        clv_text = f" | CLV {avg_clv:+.2f} ({clv_sample})" if avg_clv != "" else " | CLV building"
        lines.append(f"• {key}: {w}-{l}-{p} | {pct}% | {units:+.2f}u{clv_text}")
    return lines


def decision_report_lines(report_date=None):
    report_date = report_date or today()
    if not ENABLE_DECISION_LOG:
        return ["Decision Database: disabled"]
    rows = csv_read_rows(DECISION_LOG_FILE)
    if not rows:
        return ["Decision Database: no decisions logged yet"]
    today_rows = [r for r in rows if r.get("date") == report_date]
    graded_today = [r for r in today_rows if r.get("result") in ["WIN", "LOSS", "PUSH"]]
    pending_today = [r for r in today_rows if r.get("result") in ["", "PENDING"]]

    lines = []
    lines.append("Decision Database:")
    for action in ["BET_NOW", "TEST_UNIT", "RESEARCH_ONLY", "NO_BET"]:
        bucket = [r for r in graded_today if r.get("action") == action]
        pending = len([r for r in pending_today if r.get("action") == action])
        if bucket:
            w, l, p, pct, units, avg_clv, clv_sample = _summarize_units_and_clv(bucket)
            clv_text = f" | CLV {avg_clv:+.2f} ({clv_sample})" if avg_clv != "" else " | CLV building"
            lines.append(f"• Today {action}: {w}-{l}-{p} | {pct}% | {units:+.2f}u{clv_text} | Pending {pending}")
        elif pending:
            lines.append(f"• Today {action}: {pending} pending")

    if ENABLE_ROLLING_DECISION_DASHBOARD:
        graded = [r for r in rows if r.get("result") in ["WIN", "LOSS", "PUSH"] and r.get("action") in ["BET_NOW", "TEST_UNIT"]]
        windows = [
            ("Last 7 Days", _days_back_rows(graded, report_date, 7)),
            ("Last 30 Days", _days_back_rows(graded, report_date, 30)),
            ("Season", _season_rows(graded)),
        ]
        for title, bucket in windows:
            if not bucket:
                lines.append(f"{title}: building sample")
                continue
            w, l, p, pct, units, avg_clv, clv_sample = _summarize_units_and_clv(bucket)
            clv_text = f" | CLV {avg_clv:+.2f} ({clv_sample})" if avg_clv != "" else " | CLV building"
            lines.append(f"{title}: {w}-{l}-{p} | {pct}% | {units:+.2f}u{clv_text}")
        lines.extend(_bucket_lines("Top Season Profiles:", _season_rows(graded), limit=6))
    return lines


def adaptive_report_lines():
    if not ENABLE_ADAPTIVE_REPORTING:
        return ["Adaptive Learning: reporting disabled"]
    config = build_adaptive_config_from_results()
    lines = []
    lines.append("Adaptive Profile Learning:")
    if not config:
        lines.append("• Building sample — no graded profile decisions yet.")
    else:
        for profile, cfg in sorted(config.items(), key=lambda kv: safe_int(kv[1].get("sample"), 0), reverse=True):
            lines.append(
                f"• {profile}: {cfg.get('status')} | Sample {cfg.get('sample')} | "
                f"ROI {safe_float(cfg.get('roi'), 0):+.2%} | "
                f"CLV {safe_float(cfg.get('avg_clv'), 0):+.2f} ({cfg.get('clv_sample', 0)}) | "
                f"ConfAdj {cfg.get('confidence_adjustment')}"
            )
    if ENABLE_FEATURE_LEARNING:
        features = build_feature_learning_summary()
        proven = [f for f in features if f.get("status") in ["PROVEN", "TIGHTEN"]]
        lines.append("Feature Learning:")
        if not proven:
            lines.append(f"• Building sample — feature decisions need {MIN_FEATURE_SAMPLE}+ graded rows.")
        else:
            for f in sorted(proven, key=lambda x: abs(safe_float(x.get("units"), 0)), reverse=True)[:8]:
                lines.append(
                    f"• {f.get('feature_key')}: {f.get('status')} | Sample {f.get('sample')} | "
                    f"ROI {safe_float(f.get('roi'), 0):+.2%} | CLV {safe_float(f.get('avg_clv'), 0):+.2f} "
                    f"({f.get('clv_sample')}) | ConfAdj {f.get('confidence_adjustment')}"
                )
    lines.append(f"Adaptive safety: total confidence adjustment capped at ±{MAX_TOTAL_ADAPTIVE_CONF_ADJ}.")
    return lines


_v390_generate_daily_learning_report = generate_daily_learning_report


def generate_daily_learning_report(report_date=None):
    report_date = report_date or today()
    text = _v390_generate_daily_learning_report(report_date)
    # V3.9 returned early when no STRIKE results existed. V3.10 still reports the
    # decision database and adaptive state so test/research/no-bet tracking is visible.
    if "No graded STRIKE results found yet" in text:
        lines = text.split("\n")
        lines.append("")
        for dl in decision_report_lines(report_date):
            lines.append(dl)
        lines.append("")
        for al in adaptive_report_lines():
            lines.append(al)
        return "\n".join(lines)
    return text



# ---------------------------------------------------------------------------
# V3.10.1 / V4 Foundation Database Integrity Patch
# ---------------------------------------------------------------------------
# This patch does not change the betting model. It upgrades the research database
# so every opportunity can be linked, graded, compared, and learned from.

ENABLE_V4_FOUNDATION = os.getenv("ENABLE_V4_FOUNDATION", "true").lower() == "true"
ENABLE_OPPORTUNITY_ID = os.getenv("ENABLE_OPPORTUNITY_ID", "true").lower() == "true"
ENABLE_EDGE_PERSISTENCE = os.getenv("ENABLE_EDGE_PERSISTENCE", "true").lower() == "true"
ENABLE_REGRET_ANALYSIS = os.getenv("ENABLE_REGRET_ANALYSIS", "true").lower() == "true"
ENABLE_OPPORTUNITY_RANKING = os.getenv("ENABLE_OPPORTUNITY_RANKING", "true").lower() == "true"
ENABLE_FAIR_PRICE_FIELDS = os.getenv("ENABLE_FAIR_PRICE_FIELDS", "true").lower() == "true"
ENABLE_MARKET_MEMORY_FIELDS = os.getenv("ENABLE_MARKET_MEMORY_FIELDS", "true").lower() == "true"


def v4_profile(info=None, opportunity=None):
    opportunity = opportunity or {}
    scores = opportunity.get("scores", {}) or {}
    return market_reaction_profile_from_scores(scores, opportunity.get("scenario")) or opportunity.get("profile") or "UNCLASSIFIED"


def v4_opportunity_id(info, opportunity=None, action=""):
    """
    Universal research key shared by decision rows, strike rows, actual-entry rows,
    and CLV snapshots. It intentionally excludes reject_reason so the same market
    opportunity can be connected across decision/strike/grading tables.
    """
    if not ENABLE_OPPORTUNITY_ID:
        return ""
    info = info or {}
    opportunity = opportunity or {}
    profile = v4_profile(info, opportunity)
    side = str(opportunity.get("side") or "NONE").upper()
    line = str(opportunity.get("line") or opportunity.get("recommended_total") or "")
    inning = str(safe_int(info.get("inning"), 0))
    half = str(info.get("inning_state") or "")[:1].upper()
    outs = str(safe_int(info.get("outs"), 0))
    game_id = str(info.get("game_pk") or game_key_from_info(info) or decision_game_label(info))
    # Keep this readable for Google Sheets filtering.
    return "|".join([today(), game_id, profile, side, line, inning, half, outs])


def v4_probability_fields(opportunity, row):
    opportunity = opportunity or {}
    row = row or {}
    model_prob = safe_float(opportunity.get("model_probability"), None)
    be_prob = safe_float(opportunity.get("break_even_probability"), None)
    ev = safe_float(opportunity.get("expected_value"), None)
    edge = safe_float(row.get("edge"), safe_float(opportunity.get("edge"), 0))
    price = safe_int(row.get("price") or opportunity.get("recommended_price") or opportunity.get("price"), 0)
    if model_prob is None and edge:
        # Existing V3.2 approximation: projected run edge -> probability lift.
        model_prob = round(clamp(50 + (edge * RUN_EDGE_TO_PROB_PER_RUN * 100), 1, 99) / 100, 4)
    if be_prob is None and price:
        be_prob = round(implied_probability_from_american(price), 4)
    if ev is None and model_prob is not None and be_prob is not None:
        ev = round(model_prob - be_prob, 4)
    return model_prob, be_prob, ev


def fair_price_from_probability(prob):
    p = safe_float(prob, None)
    if p is None or p <= 0 or p >= 1:
        return ""
    if p >= 0.5:
        return int(round(-100 * p / (1 - p)))
    return int(round(100 * (1 - p) / p))


def fair_total_from_projection(projection):
    proj = safe_float(projection, None)
    if proj is None:
        return ""
    return round(proj * 2) / 2


_v310_decision_log_fieldnames = decision_log_fieldnames


def decision_log_fieldnames():
    fields = list(_v310_decision_log_fieldnames())
    additions = [
        # Linkage / identity
        "opportunity_id", "parent_decision_id", "linked_strike_id", "source_bot_version", "source_app_mode",
        # Board/ranking shell for V4
        "board_rank", "opportunity_score", "top_5_flag", "best_available_market",
        # Fair price / probability
        "fair_total", "fair_price", "fair_probability", "model_probability", "break_even_probability",
        "ev_edge", "probability_source",
        # Edge persistence / market memory
        "edge_first_seen", "edge_last_seen", "edge_duration_seconds", "max_edge_seen",
        "opening_to_alert_move", "alert_to_close_move", "best_clv_seen", "worst_clv_seen",
        "beat_market", "market_memory_match", "market_memory_sample", "market_memory_roi", "market_memory_avg_clv",
        # Regret / rejected opportunity audit
        "regret_flag", "would_have_result", "would_have_units", "missed_value_reason",
        # Actual user execution
        "actual_bet_placed", "actual_entry_line", "actual_entry_price", "actual_entry_book",
        "actual_entry_time", "actual_wager_units", "actual_result",
    ]
    for f in additions:
        if f not in fields:
            fields.append(f)
    return fields


_v310_decision_row_from_opportunity = decision_row_from_opportunity


def decision_row_from_opportunity(info, market_context, opportunity, action, decision_type="", reject_reason=""):
    row = _v310_decision_row_from_opportunity(info, market_context, opportunity, action, decision_type, reject_reason)
    if not ENABLE_V4_FOUNDATION:
        return row
    info = info or {}
    market_context = market_context or {}
    opportunity = opportunity or {}
    model_prob, be_prob, ev = v4_probability_fields(opportunity, row)
    fair_price = fair_price_from_probability(model_prob)
    projected = row.get("projected_total") or opportunity.get("projected_total") or opportunity.get("projection")
    live_total = safe_float(row.get("live_total"), safe_float(market_context.get("live_total"), None))
    opening_total = safe_float(row.get("opening_total"), safe_float(market_context.get("opening_total"), None))
    line = safe_float(row.get("line"), safe_float(opportunity.get("recommended_total"), None))
    edge = safe_float(row.get("edge"), 0)
    move_to_alert = ""
    if live_total is not None and opening_total is not None:
        move_to_alert = round(live_total - opening_total, 1)
    row.update({
        "opportunity_id": v4_opportunity_id(info, opportunity, action),
        "parent_decision_id": row.get("decision_id", ""),
        "source_bot_version": APP_VERSION,
        "source_app_mode": APP_MODE,
        "board_rank": opportunity.get("board_rank", ""),
        "opportunity_score": opportunity.get("opportunity_score", row.get("confidence", "")),
        "top_5_flag": opportunity.get("top_5_flag", ""),
        "best_available_market": opportunity.get("best_available_market", "totals"),
        "fair_total": opportunity.get("fair_total", fair_total_from_projection(projected)),
        "fair_price": opportunity.get("fair_price", fair_price),
        "fair_probability": opportunity.get("fair_probability", model_prob),
        "model_probability": model_prob,
        "break_even_probability": be_prob,
        "ev_edge": ev,
        "probability_source": opportunity.get("probability_source", "v4_formula" if model_prob is not None else ""),
        "edge_first_seen": row.get("timestamp"),
        "edge_last_seen": row.get("timestamp"),
        "edge_duration_seconds": 0,
        "max_edge_seen": edge,
        "opening_to_alert_move": move_to_alert,
        "alert_to_close_move": "",
        "best_clv_seen": row.get("clv", ""),
        "worst_clv_seen": row.get("clv", ""),
        "beat_market": "",
        "market_memory_match": "",
        "market_memory_sample": "",
        "market_memory_roi": "",
        "market_memory_avg_clv": "",
        "regret_flag": "YES" if action == "NO_BET" else "",
        "would_have_result": "",
        "would_have_units": "",
        "missed_value_reason": reject_reason if action == "NO_BET" else "",
        "actual_bet_placed": "",
        "actual_entry_line": "",
        "actual_entry_price": "",
        "actual_entry_book": "",
        "actual_entry_time": "",
        "actual_wager_units": "",
        "actual_result": "",
    })
    # If line is missing, preserve blanks rather than inventing.
    if line is None:
        row["fair_total"] = row.get("fair_total") or ""
    return row


_v310_update_decision_log_clv_snapshots = update_decision_log_clv_snapshots


def update_decision_log_clv_snapshots(info, live_total):
    _v310_update_decision_log_clv_snapshots(info, live_total)
    if not (ENABLE_V4_FOUNDATION and ENABLE_EDGE_PERSISTENCE and ENABLE_DECISION_LOG):
        return
    current_line = safe_float(live_total, None)
    if current_line is None:
        return
    rows = csv_read_rows(DECISION_LOG_FILE)
    if not rows:
        return
    info = info or {}
    now_iso = now_local().isoformat()
    changed = False
    for row in rows:
        if row.get("date") != today():
            continue
        if row.get("result") in ["WIN", "LOSS", "PUSH"]:
            continue
        same_game = row.get("game_pk") == str(info.get("game_pk")) or row.get("game") == f"{info.get('away')} at {info.get('home')}"
        if not same_game:
            continue
        side = str(row.get("side", "")).upper()
        alert_line = safe_float(row.get("line"), None)
        if side not in ["OVER", "UNDER"] or alert_line is None:
            continue
        clv = round(current_line - alert_line, 1) if side == "OVER" else round(alert_line - current_line, 1)
        first_seen = row.get("edge_first_seen") or row.get("timestamp") or now_iso
        try:
            start_dt = datetime.fromisoformat(str(first_seen))
            duration = max(0, int((now_local() - start_dt).total_seconds()))
        except Exception:
            duration = safe_int(row.get("edge_duration_seconds"), 0)
        old_best = safe_float(row.get("best_clv_seen"), None)
        old_worst = safe_float(row.get("worst_clv_seen"), None)
        row["edge_last_seen"] = now_iso
        row["edge_duration_seconds"] = duration
        row["alert_to_close_move"] = clv
        row["best_clv_seen"] = clv if old_best is None else max(old_best, clv)
        row["worst_clv_seen"] = clv if old_worst is None else min(old_worst, clv)
        row["beat_market"] = "TRUE" if clv > 0 else "FALSE"
        max_edge = safe_float(row.get("max_edge_seen"), None)
        current_edge = safe_float(row.get("edge"), None)
        if current_edge is not None:
            row["max_edge_seen"] = current_edge if max_edge is None else max(max_edge, current_edge)
        changed = True
    if changed:
        csv_write_rows(DECISION_LOG_FILE, decision_log_fieldnames(), rows)


_v310_grade_completed_decision_log = grade_completed_decision_log


def grade_completed_decision_log(game_pk, label, final_score):
    """Grade every decision row and add V4 regret/would-have fields."""
    _v310_grade_completed_decision_log(game_pk, label, final_score)
    if not (ENABLE_V4_FOUNDATION and ENABLE_DECISION_LOG):
        return
    final_total = final_total_from_score(final_score)
    if final_total is None:
        return
    rows = csv_read_rows(DECISION_LOG_FILE)
    if not rows:
        return
    changed = False
    for row in rows:
        same_game = (
            str(row.get("game_pk")) == str(game_pk)
            or row.get("game") == label
            or row.get("game_key") == f"{today()}::{label}"
        )
        if not same_game:
            continue
        side = str(row.get("side", "")).upper()
        line = safe_float(row.get("line"), None)
        if side not in ["OVER", "UNDER"] or line is None:
            continue
        result = grade_bet(side, line, final_total)
        units = american_odds_profit_units(row.get("price"), result)
        # Make sure even old rows get completed.
        row["final_score"] = final_score
        row["final_total"] = final_total
        row["result"] = row.get("result") if row.get("result") in ["WIN", "LOSS", "PUSH"] else result
        row["units"] = row.get("units") if str(row.get("units", "")).strip() else units
        row["graded_at"] = row.get("graded_at") or now_local().isoformat()
        if row.get("action") == "NO_BET":
            row["regret_flag"] = "YES"
            row["would_have_result"] = result
            row["would_have_units"] = units
        else:
            row["actual_result"] = result if row.get("actual_bet_placed") else row.get("actual_result", "")
        clv = safe_float(row.get("clv"), None)
        if clv is not None:
            row["beat_market"] = "TRUE" if clv > 0 else "FALSE"
            row["best_clv_seen"] = row.get("best_clv_seen") or clv
            row["worst_clv_seen"] = row.get("worst_clv_seen") or clv
        changed = True
    if changed:
        csv_write_rows(DECISION_LOG_FILE, decision_log_fieldnames(), rows)
        build_adaptive_config_from_results()
        print(f"V4 DECISION DATABASE GRADED | {label} | Final {final_score}")


# Expand actual entry sheet for real BetMGM execution tracking while preserving old templates.
_v310_actual_entry_fieldnames = actual_entry_fieldnames if 'actual_entry_fieldnames' in globals() else None


def actual_entry_fieldnames():
    base = _v310_actual_entry_fieldnames() if _v310_actual_entry_fieldnames else []
    if not base:
        base = ["strike_id", "date", "game", "side", "line", "price", "book"]
    for f in [
        "opportunity_id", "did_bet", "actual_entry_line", "actual_entry_price", "actual_entry_book",
        "actual_entry_time", "actual_wager_units", "notes"
    ]:
        if f not in base:
            base.append(f)
    return base


_v310_actual_entry_template_from_strike = actual_entry_template_from_strike if 'actual_entry_template_from_strike' in globals() else None


def actual_entry_template_from_strike(row):
    base = _v310_actual_entry_template_from_strike(row) if _v310_actual_entry_template_from_strike else dict(row or {})
    base = dict(base or {})
    base.setdefault("opportunity_id", (row or {}).get("opportunity_id", ""))
    base.setdefault("did_bet", "")
    base.setdefault("actual_entry_line", "")
    base.setdefault("actual_entry_price", "")
    base.setdefault("actual_entry_book", "")
    base.setdefault("actual_entry_time", "")
    base.setdefault("actual_wager_units", "")
    base.setdefault("notes", "")
    return base



# ---------------------------------------------------------------------------
# V3.10.2 EXISTING TABS IMPORTANT DATA EXPORT PATCH
# ---------------------------------------------------------------------------
# Purpose:
# Your Google Sheet already has the correct tabs. This patch sends the important
# business-process outputs to those EXISTING event_type tabs instead of creating
# new tab names. It does not add API calls. It uses the CSV/database rows the bot
# already creates, then mirrors analyst-ready summaries through TRACKING_WEBHOOK_URL.

IMPORTANT_EXPORT_STATE_FILE = os.getenv("IMPORTANT_EXPORT_STATE_FILE", "important_data_export_state.json")
ENABLE_EXISTING_TAB_IMPORTANT_EXPORTS = os.getenv("ENABLE_EXISTING_TAB_IMPORTANT_EXPORTS", "true").lower() == "true"
ENABLE_OPPORTUNITY_RANKING = os.getenv("ENABLE_OPPORTUNITY_RANKING", "true").lower() == "true"
ENABLE_EDGE_PERSISTENCE = os.getenv("ENABLE_EDGE_PERSISTENCE", "true").lower() == "true"
ENABLE_REGRET_ANALYSIS = os.getenv("ENABLE_REGRET_ANALYSIS", "true").lower() == "true"
ENABLE_PROFILE_SUMMARY_EXPORT = os.getenv("ENABLE_PROFILE_SUMMARY_EXPORT", "true").lower() == "true"
ENABLE_ADAPTIVE_PROFILE_EXPORT = os.getenv("ENABLE_ADAPTIVE_PROFILE_EXPORT", "true").lower() == "true"
ENABLE_FEATURE_LEARNING_EXPORT = os.getenv("ENABLE_FEATURE_LEARNING_EXPORT", "true").lower() == "true"
IMPORTANT_EXPORT_TOP_N = int(os.getenv("IMPORTANT_EXPORT_TOP_N", "25"))

CORE_VARIABLES_FOR_AUDIT = [
    "market_confirmation_score", "market_value_score", "line_velocity",
    "edge", "expected_value", "pressure_to_runs", "run_conversion",
    "traffic_conversion", "pitcher_stress", "risk_filter_score",
    "contact_quality", "bullpen_risk", "run_prevention", "strikeout_environment",
    "bullpen_lockdown", "continuation_score", "discounted_over_score",
    "false_inflation_score", "continuation_exhaustion_score",
    "pitching_dominance_under_score", "market_reaction_move", "market_discount",
]


def important_export_enabled():
    return bool(ENABLE_EXISTING_TAB_IMPORTANT_EXPORTS and tracking_webhook_enabled())


def _important_state_load():
    try:
        if os.path.exists(IMPORTANT_EXPORT_STATE_FILE):
            with open(IMPORTANT_EXPORT_STATE_FILE, "r") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception as e:
        print("IMPORTANT EXPORT STATE LOAD ERROR:", repr(e))
    return {}


def _important_state_save(data):
    try:
        with open(IMPORTANT_EXPORT_STATE_FILE, "w") as f:
            json.dump(data or {}, f, indent=2)
    except Exception as e:
        print("IMPORTANT EXPORT STATE SAVE ERROR:", repr(e))


def _row_result_and_units(row):
    """Return the outcome for accepted bets OR would-have outcome for NO_BET rows."""
    row = row or {}
    action = str(row.get("action") or "").upper()
    if action == "NO_BET":
        result = row.get("would_have_result") or row.get("result")
        units = row.get("would_have_units") if str(row.get("would_have_units", "")).strip() else row.get("units")
    else:
        result = row.get("result") or row.get("actual_result")
        units = row.get("units")
    result = str(result or "").upper()
    if result not in ["WIN", "LOSS", "PUSH"]:
        return "", None
    return result, safe_float(units, 0)


def _decision_rows_for_date(report_date=None, graded_only=False):
    report_date = report_date or today()
    rows = [r for r in csv_read_rows(DECISION_LOG_FILE) if r.get("date") == report_date]
    if graded_only:
        out = []
        for r in rows:
            res, _units = _row_result_and_units(r)
            if res in ["WIN", "LOSS", "PUSH"]:
                out.append(r)
        return out
    return rows


def _summarize_outcome_rows(rows):
    wins = losses = pushes = 0
    units = 0.0
    clvs = []
    for r in rows or []:
        res, u = _row_result_and_units(r)
        if res == "WIN":
            wins += 1
        elif res == "LOSS":
            losses += 1
        elif res == "PUSH":
            pushes += 1
        else:
            continue
        units += safe_float(u, 0)
        if str(r.get("clv", "")).strip() not in ["", "None"]:
            clvs.append(safe_float(r.get("clv"), 0))
    sample = wins + losses + pushes
    win_pct = round((wins / max(1, wins + losses)) * 100, 1) if (wins + losses) else 0
    roi = round(units / max(1, wins + losses), 4) if (wins + losses) else 0
    avg_clv = round(avg(clvs), 2) if clvs else 0.0
    return {
        "sample": sample, "wins": wins, "losses": losses, "pushes": pushes,
        "win_pct": win_pct, "units": round(units, 2), "roi": roi,
        "avg_clv": avg_clv, "clv_sample": len(clvs),
    }


def _audit_verdict(sample, roi, avg_clv):
    sample = safe_int(sample, 0)
    roi = safe_float(roi, 0)
    avg_clv = safe_float(avg_clv, 0)
    if sample < 30:
        return "BUILD_SAMPLE"
    if roi >= 0.04 and avg_clv >= 0.20:
        return "PROMOTE"
    if roi <= -0.03 and avg_clv <= -0.20:
        return "TIGHTEN_OR_REMOVE"
    if roi > 0:
        return "LEAN_KEEP"
    if roi < 0:
        return "LEAN_DOWNGRADE"
    return "HOLD"


def _short_reason(row):
    reason = str((row or {}).get("reject_reason") or (row or {}).get("quality_reason") or "UNSPECIFIED")
    return reason[:140]


def _decision_event_base(row, report_date=None, export_type=""):
    row = row or {}
    result, units = _row_result_and_units(row)
    return {
        "date": report_date or row.get("date") or today(),
        "export_type": export_type,
        "decision_id": row.get("decision_id"),
        "timestamp": row.get("timestamp"),
        "game": row.get("game"),
        "game_pk": row.get("game_pk"),
        "action": row.get("action"),
        "decision_type": row.get("decision_type"),
        "reject_reason": row.get("reject_reason"),
        "profile": row.get("profile") or row.get("market_reaction_profile"),
        "scenario": row.get("scenario"),
        "side": row.get("side"),
        "line": row.get("line"),
        "price": row.get("price"),
        "book": row.get("book") or row.get("recommended_book"),
        "inning": row.get("inning"),
        "inning_state": row.get("inning_state"),
        "outs": row.get("outs"),
        "score": row.get("score"),
        "base_out": row.get("base_out"),
        "opening_total": row.get("opening_total"),
        "live_total": row.get("live_total"),
        "projected_total": row.get("projected_total"),
        "edge": row.get("edge"),
        "expected_value": row.get("expected_value"),
        "confidence": row.get("confidence"),
        "market_confirmation_score": row.get("market_confirmation_score"),
        "market_value_score": row.get("market_value_score"),
        "risk_filter_score": row.get("risk_filter_score"),
        "line_velocity": row.get("line_velocity"),
        "line_direction": row.get("line_direction"),
        "market_disagreement": row.get("market_disagreement"),
        "recommended_book": row.get("recommended_book"),
        "recommended_total": row.get("recommended_total"),
        "recommended_price": row.get("recommended_price"),
        "best_line_age_seconds": row.get("best_line_age_seconds"),
        "calculated_risk_tier": row.get("calculated_risk_tier"),
        "suggested_unit": row.get("suggested_unit"),
        "pattern_tags": row.get("pattern_tags"),
        "bet_quality": row.get("bet_quality"),
        "quality_reason": row.get("quality_reason"),
        "final_score": row.get("final_score"),
        "final_total": row.get("final_total"),
        "result": result or row.get("result"),
        "units": units if units is not None else row.get("units"),
        "clv": row.get("clv"),
        "would_have_result": row.get("would_have_result"),
        "would_have_units": row.get("would_have_units"),
        "updated_at": now_local().isoformat(),
    }


def _opportunity_score(row):
    edge = abs(safe_float(row.get("edge"), 0))
    ev = max(0, safe_float(row.get("expected_value"), 0))
    conf = safe_float(row.get("confidence"), 0)
    mkt = safe_float(row.get("market_confirmation_score"), 0)
    value = safe_float(row.get("market_value_score"), 0)
    risk = safe_float(row.get("risk_filter_score"), 50)
    clv = safe_float(row.get("clv"), 0)
    return round(edge * 8 + ev * 40 + conf * 0.22 + mkt * 0.18 + value * 0.20 - risk * 0.15 + clv * 2, 2)


def post_existing_tab_decision_exports(row):
    """Real-time mirrors for the existing opportunity/edge tabs, called when shift_decision logs."""
    if not important_export_enabled() or not row:
        return
    if ENABLE_OPPORTUNITY_RANKING:
        opp = _decision_event_base(row, row.get("date"), "real_time_decision")
        opp["opportunity_score"] = _opportunity_score(row)
        opp["board_rank"] = ""
        opp["top_5_flag"] = ""
        post_tracking_event("opportunity_ranking", opp)
    if ENABLE_EDGE_PERSISTENCE:
        edge = _decision_event_base(row, row.get("date"), "real_time_edge")
        edge.update({
            "edge_first_seen": row.get("edge_first_seen") or row.get("timestamp"),
            "edge_last_seen": row.get("edge_last_seen") or row.get("timestamp"),
            "edge_duration_seconds": row.get("edge_duration_seconds") or "",
            "max_edge_seen": row.get("max_edge_seen") or row.get("edge"),
            "opening_to_alert_move": row.get("market_reaction_move"),
            "alert_to_close_move": row.get("clv"),
            "best_clv_seen": row.get("best_clv_seen") or row.get("clv"),
            "worst_clv_seen": row.get("worst_clv_seen") or row.get("clv"),
            "beat_market": row.get("beat_market"),
        })
        post_tracking_event("edge_persistence", edge)


def _send_opportunity_ranking_export(report_date):
    if not ENABLE_OPPORTUNITY_RANKING:
        return 0
    rows = _decision_rows_for_date(report_date, graded_only=False)
    ranked = sorted(rows, key=_opportunity_score, reverse=True)[:IMPORTANT_EXPORT_TOP_N]
    sent = 0
    for rank, r in enumerate(ranked, start=1):
        payload = _decision_event_base(r, report_date, "nightly_top_opportunity")
        payload["board_rank"] = rank
        payload["opportunity_score"] = _opportunity_score(r)
        payload["top_5_flag"] = "TRUE" if rank <= 5 else "FALSE"
        post_tracking_event("opportunity_ranking", payload)
        sent += 1
    return sent


def _send_edge_persistence_export(report_date):
    if not ENABLE_EDGE_PERSISTENCE:
        return 0
    rows = _decision_rows_for_date(report_date, graded_only=False)
    rows = sorted(rows, key=lambda r: abs(safe_float(r.get("edge"), 0)), reverse=True)[:IMPORTANT_EXPORT_TOP_N]
    sent = 0
    for r in rows:
        payload = _decision_event_base(r, report_date, "nightly_edge_persistence")
        payload.update({
            "edge_first_seen": r.get("edge_first_seen") or r.get("timestamp"),
            "edge_last_seen": r.get("edge_last_seen") or r.get("graded_at") or r.get("timestamp"),
            "edge_duration_seconds": r.get("edge_duration_seconds") or "",
            "max_edge_seen": r.get("max_edge_seen") or r.get("edge"),
            "opening_to_alert_move": r.get("market_reaction_move"),
            "alert_to_close_move": r.get("clv"),
            "best_clv_seen": r.get("best_clv_seen") or r.get("clv"),
            "worst_clv_seen": r.get("worst_clv_seen") or r.get("clv"),
            "beat_market": r.get("beat_market"),
        })
        post_tracking_event("edge_persistence", payload)
        sent += 1
    return sent


def _send_regret_analysis_export(report_date):
    if not ENABLE_REGRET_ANALYSIS:
        return 0
    rows = _decision_rows_for_date(report_date, graded_only=True)
    no_bets = [r for r in rows if str(r.get("action") or "").upper() == "NO_BET"]
    sent = 0

    # Filter audit: which rejection reasons saved or cost money.
    buckets = {}
    for r in no_bets:
        key = _short_reason(r)
        buckets.setdefault(key, []).append(r)
    for reason, bucket in sorted(buckets.items(), key=lambda kv: abs(_summarize_outcome_rows(kv[1])["units"]), reverse=True)[:IMPORTANT_EXPORT_TOP_N]:
        s = _summarize_outcome_rows(bucket)
        payload = {
            "date": report_date,
            "export_type": "filter_summary",
            "reject_reason": reason,
            **s,
            "verdict": "GOOD_FILTER" if s["units"] < 0 else "COSTLY_FILTER" if s["units"] > 0 else "NEUTRAL_FILTER",
            "updated_at": now_local().isoformat(),
        }
        post_tracking_event("regret_analysis", payload)
        sent += 1

    # Top missed winners and top rejected losers.
    sorted_regret = sorted(no_bets, key=lambda r: safe_float(r.get("would_have_units") or r.get("units"), 0), reverse=True)
    candidates = sorted_regret[:10] + list(reversed(sorted_regret[-10:]))
    seen = set()
    for r in candidates:
        did = r.get("decision_id") or json.dumps(r, sort_keys=True)[:120]
        if did in seen:
            continue
        seen.add(did)
        payload = _decision_event_base(r, report_date, "missed_or_saved_opportunity")
        wh_units = safe_float(r.get("would_have_units") or r.get("units"), 0)
        payload["regret_flag"] = "YES" if wh_units > 0 else "SAVED_BY_FILTER" if wh_units < 0 else "NEUTRAL"
        payload["missed_value_reason"] = _short_reason(r)
        payload["would_have_units"] = wh_units
        post_tracking_event("regret_analysis", payload)
        sent += 1
    return sent


def _send_feature_learning_export(report_date):
    if not ENABLE_FEATURE_LEARNING_EXPORT:
        return 0
    rows = _decision_rows_for_date(report_date, graded_only=True)
    buckets = {}
    for r in rows:
        tags = [t for t in str(r.get("pattern_tags") or "").split("|") if t]
        for tag in tags:
            buckets.setdefault("TAG:" + tag, []).append(r)
        for var in CORE_VARIABLES_FOR_AUDIT:
            val = safe_float(r.get(var), None)
            if val is None:
                continue
            if var in ["edge", "expected_value", "line_velocity", "market_reaction_move", "market_discount"]:
                if abs(val) >= 3:
                    bucket = "HIGH_ABS"
                elif abs(val) >= 1:
                    bucket = "MID_ABS"
                else:
                    bucket = "LOW_ABS"
            else:
                if val >= 85:
                    bucket = "85_PLUS"
                elif val >= 70:
                    bucket = "70_84"
                elif val >= 55:
                    bucket = "55_69"
                else:
                    bucket = "UNDER_55"
            buckets.setdefault(f"{var}:{bucket}", []).append(r)
    sent = 0
    for feature_key, bucket_rows in sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)[:IMPORTANT_EXPORT_TOP_N * 2]:
        s = _summarize_outcome_rows(bucket_rows)
        payload = {
            "date": report_date,
            "feature_key": feature_key,
            **s,
            "status": _audit_verdict(s["sample"], s["roi"], s["avg_clv"]),
            "confidence_adjustment": "",
            "updated_at": now_local().isoformat(),
        }
        post_tracking_event("feature_learning", payload)
        sent += 1
    return sent


def _send_profile_exports(report_date):
    rows = _decision_rows_for_date(report_date, graded_only=True)
    sent = 0
    profile_buckets = {}
    for r in rows:
        p = r.get("profile") or r.get("market_reaction_profile") or "UNCLASSIFIED"
        profile_buckets.setdefault(p, []).append(r)
    if ENABLE_PROFILE_SUMMARY_EXPORT:
        for profile, bucket in sorted(profile_buckets.items(), key=lambda kv: len(kv[1]), reverse=True):
            s = _summarize_outcome_rows(bucket)
            payload = {
                "date": report_date,
                "profile": profile,
                **s,
                "status": _audit_verdict(s["sample"], s["roi"], s["avg_clv"]),
                "updated_at": now_local().isoformat(),
            }
            post_tracking_event("profile_summary", payload)
            sent += 1
    if ENABLE_ADAPTIVE_PROFILE_EXPORT:
        try:
            config = build_adaptive_config_from_results() or load_adaptive_config()
        except Exception:
            config = load_adaptive_config()
        if isinstance(config, dict):
            for profile, data in config.items():
                payload = {"date": report_date, "profile": profile, **(data or {}), "updated_at": now_local().isoformat()}
                post_tracking_event("adaptive_profile", payload)
                sent += 1
    return sent


def _send_opportunity_cost_export(report_date):
    # Uses regret_analysis tab because that is where accepted vs passed decision economics belong.
    if not ENABLE_REGRET_ANALYSIS:
        return 0
    rows = _decision_rows_for_date(report_date, graded_only=True)
    accepted = [r for r in rows if str(r.get("action") or "").upper() in ["BET_NOW", "TEST_UNIT"]]
    rejected = [r for r in rows if str(r.get("action") or "").upper() in ["NO_BET", "RESEARCH_ONLY"]]
    sent = 0
    for label, bucket in [("accepted_bets", accepted), ("passed_or_research", rejected)]:
        s = _summarize_outcome_rows(bucket)
        payload = {
            "date": report_date,
            "export_type": "opportunity_cost",
            "decision_bucket": label,
            **s,
            "updated_at": now_local().isoformat(),
        }
        post_tracking_event("regret_analysis", payload)
        sent += 1
    return sent


def send_existing_tab_important_data_exports(report_date=None, force=False):
    report_date = report_date or today()
    if not important_export_enabled():
        print("IMPORTANT DATA EXPORT SKIPPED: webhook disabled or missing.")
        return False
    state = _important_state_load()
    sent_key = f"existing_tab_important_exports_sent_for_{report_date}"
    if state.get(sent_key) and not force:
        print(f"IMPORTANT DATA EXPORT SKIPPED: already sent for {report_date}")
        return False
    counts = {}
    try:
        counts["opportunity_ranking"] = _send_opportunity_ranking_export(report_date)
        counts["edge_persistence"] = _send_edge_persistence_export(report_date)
        counts["regret_analysis"] = _send_regret_analysis_export(report_date) + _send_opportunity_cost_export(report_date)
        counts["feature_learning"] = _send_feature_learning_export(report_date)
        counts["profile_adaptive"] = _send_profile_exports(report_date)
        state[sent_key] = {"sent_at": now_local().isoformat(), "counts": counts}
        _important_state_save(state)
        print(f"IMPORTANT DATA EXPORT COMPLETE | {report_date} | {counts}")
        return True
    except Exception as e:
        print("IMPORTANT DATA EXPORT ERROR:", repr(e))
        return False


# Wrap the daily report so nightly business-process exports happen with the existing end-of-night routine.
_v3102_generate_daily_learning_report = generate_daily_learning_report

def generate_daily_learning_report(report_date=None):
    report_date = report_date or today()
    report = _v3102_generate_daily_learning_report(report_date)
    send_existing_tab_important_data_exports(report_date, force=False)
    return report



if __name__ == "__main__":
    main()
