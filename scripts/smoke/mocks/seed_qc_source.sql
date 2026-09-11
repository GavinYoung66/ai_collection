-- P-10：与 qc_source.json / DD-02 §4.6.1 同构的主数据（qc 禁止 INSERT 这两张通话表）
-- session_id=sess_fixture_001
-- call_records.id = call_id = 22222222-3333-4444-5555-666666666666

INSERT INTO cases (
    id, case_number, customer_name, phone_encrypted, phone_hash,
    overdue_amount, overdue_days, account_stage
) VALUES (
    '11111111-2222-3333-4444-555555555555'::uuid,
    'FIXTURE-QC-001',
    '张三',
    'enc',
    'fixturehash0000000000000000000000000000000000000000000000000000',
    3800.00,
    32,
    'M1'
)
ON CONFLICT (id) DO UPDATE SET
    customer_name = EXCLUDED.customer_name,
    overdue_amount = EXCLUDED.overdue_amount,
    overdue_days = EXCLUDED.overdue_days,
    is_delete = '0';

INSERT INTO call_records (
    id, session_id, case_id, call_type, call_status,
    start_time, end_time, duration_seconds, result
) VALUES (
    '22222222-3333-4444-5555-666666666666'::uuid,
    'sess_fixture_001',
    '11111111-2222-3333-4444-555555555555'::uuid,
    'ai',
    'completed',
    NOW() - INTERVAL '8 minutes',
    NOW(),
    480,
    NULL
)
ON CONFLICT (session_id) DO UPDATE SET
    case_id = EXCLUDED.case_id,
    call_type = EXCLUDED.call_type,
    call_status = EXCLUDED.call_status,
    start_time = EXCLUDED.start_time,
    end_time = EXCLUDED.end_time,
    duration_seconds = EXCLUDED.duration_seconds,
    result = EXCLUDED.result,
    is_delete = '0';

DELETE FROM dialog_turns WHERE session_id = 'sess_fixture_001';
INSERT INTO dialog_turns (session_id, turn_number, speaker, text) VALUES
    ('sess_fixture_001', 1, 'ai', '您好，请问是张三先生吗？'),
    ('sess_fixture_001', 2, 'user', '是我。');
