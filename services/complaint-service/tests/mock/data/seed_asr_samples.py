"""种子 ASR 通话样本：覆盖正常 / ASR 残缺 / 多案件 / 重复投诉场景。"""
from __future__ import annotations

SEED_ASR_SAMPLES = {
    "C-2026-0001": [
        {
            "call_session_id": "S-0001",
            "call_start_time": "2026-08-15T23:05:00+08:00",
            "call_duration": 180,
            "agent_role": "agent",
            "asr_text_with_timestamp": [
                {"ts": "23:05:01", "speaker": "agent", "text": "你要是再不还钱，我就去你家找你老婆孩子。"},
                {"ts": "23:05:20", "speaker": "user", "text": "你们这是威胁，我要投诉。"},
            ],
            "is_corrupted": False,
        }
    ],
    "C-2026-0002": [
        {
            "call_session_id": "S-0002",
            "call_start_time": "2026-08-10T14:00:00+08:00",
            "call_duration": 120,
            "agent_role": "agent",
            "asr_text_with_timestamp": [
                {"ts": "14:00:01", "speaker": "agent", "text": "您好，您的账单已出，请及时还款。"},
                {"ts": "14:00:30", "speaker": "user", "text": "我已经还了，怎么还扣钱？"},
            ],
            "is_corrupted": False,
        }
    ],
    "C-2026-0003": [
        {
            "call_session_id": "S-0003",
            "call_start_time": "2026-08-12T10:00:00+08:00",
            "call_duration": 90,
            "agent_role": "agent",
            "asr_text_with_timestamp": [
                {"ts": "10:00:01", "speaker": "agent", "text": "赶紧还钱，别废话。"},
            ],
            "is_corrupted": False,
        }
    ],
    "C-2026-0004-A": [
        {
            "call_session_id": "S-0004-A",
            "call_start_time": "2026-08-20T09:00:00+08:00",
            "call_duration": 60,
            "agent_role": "agent",
            "asr_text_with_timestamp": [
                {"ts": "09:00:01", "speaker": "agent", "text": "三笔贷款都逾期了，赶紧处理。"},
            ],
            "is_corrupted": False,
        }
    ],
    "C-2026-0004-B": [
        {
            "call_session_id": "S-0004-B",
            "call_start_time": "2026-08-20T09:05:00+08:00",
            "call_duration": 45,
            "agent_role": "agent",
            "asr_text_with_timestamp": [
                {"ts": "09:05:01", "speaker": "agent", "text": "第二笔也逾期了。"},
            ],
            "is_corrupted": False,
        }
    ],
    "C-2026-0004-C": [
        {
            "call_session_id": "S-0004-C",
            "call_start_time": "2026-08-20T23:30:00+08:00",
            "call_duration": 30,
            "agent_role": "agent",
            "asr_text_with_timestamp": [
                {"ts": "23:30:01", "speaker": "agent", "text": "半夜也得还钱。"},
            ],
            "is_corrupted": False,
        }
    ],
    "C-2026-0005": [
        {
            "call_session_id": "S-0005",
            "call_start_time": "2026-08-25T15:00:00+08:00",
            "call_duration": 100,
            "agent_role": "agent",
            "asr_text_with_timestamp": [
                {"ts": "15:00:01", "speaker": "agent", "text": "上次说的方案您考虑得怎么样？"},
            ],
            "is_corrupted": False,
        }
    ],
    "C-2026-0006": [
        {
            "call_session_id": "S-0006",
            "call_start_time": "2026-08-28T16:00:00+08:00",
            "call_duration": 200,
            "agent_role": "agent",
            "asr_text_with_timestamp": [
                {"ts": "16:00:01", "speaker": "agent", "text": "###***&&&...###"},
                {"ts": "16:00:30", "speaker": "user", "text": "你说什么我听不清。"},
            ],
            "is_corrupted": True,
        }
    ],
    "C-2026-0007": [
        {
            "call_session_id": "S-0007",
            "call_start_time": "2026-08-30T11:00:00+08:00",
            "call_duration": 150,
            "agent_role": "agent",
            "asr_text_with_timestamp": [
                {"ts": "11:00:01", "speaker": "agent", "text": "您还欠 5000 元。"},
                {"ts": "11:00:20", "speaker": "user", "text": "不对，系统显示我只欠 3000。"},
            ],
            "is_corrupted": False,
        }
    ],
    "C-2026-0008": [
        {
            "call_session_id": "S-0008",
            "call_start_time": "2026-08-28T14:00:00+08:00",
            "call_duration": 220,
            "agent_role": "agent",
            "asr_text_with_timestamp": [
                {"ts": "14:00:01", "speaker": "agent", "text": "我是法院的，你有一张传票，不还钱就坐牢。"},
                {"ts": "14:00:40", "speaker": "agent", "text": "我已经把你的信息告诉你邻居了。"},
            ],
            "is_corrupted": False,
        }
    ],
}


def get_asr_by_case(case_id: str) -> list[dict]:
    return SEED_ASR_SAMPLES.get(case_id, [])
