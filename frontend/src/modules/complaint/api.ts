/**
 * 智能投诉模块 API 层：SSOT §5.0 B 表 16 端点逐条接入，一律 /api/v1/complaints/* 全前缀。
 * 只 import shared/request 与 ./types；超时只引用 TIMEOUT 常量表（judge→TIMEOUT.judge，
 * 其余→TIMEOUT.normal，SSOT §9.3）；禁止出现超时数值字面量；stats 与 mode 也必须
 * 走 complaints 全前缀，禁止脱离前缀的孤儿路由写法。响应拦截器已解包信封，故用第二
 * 泛型 R 直接声明 data 类型。
 */
import http, { TIMEOUT } from '@/shared/request'
import type {
  AgentLogsResponse,
  CloseRequest,
  CloseResponse,
  ComplaintCreateRequest,
  ComplaintCreateResponse,
  ComplaintDetailResponse,
  ComplaintEventsResponse,
  ComplaintListQuery,
  ComplaintListResponse,
  DemoSeedResponse,
  DispositionCallbackRequest,
  DispositionCallbackResponse,
  DispositionVerdictRequest,
  DispositionVerdictResponse,
  JudgeRequest,
  JudgeResponse,
  ModeSwitchRequest,
  ModeSwitchResponse,
  ReviewRequest,
  ReviewResponse,
  StatsResponse,
  StatusResponse,
  TraceResponse,
  VerdictResponse,
} from './types'

/** 列表分页默认值与上限（后端 complaints.py:89-90：page≥1、page_size 1~100 默认 20） */
const DEFAULT_PAGE = 1
const DEFAULT_PAGE_SIZE = 20
const MAX_PAGE_SIZE = 100

/** 1. 投诉单列表 GET /api/v1/complaints（complaints.py:87） */
export function listComplaints(query: ComplaintListQuery = {}): Promise<ComplaintListResponse> {
  const page = query.page ?? DEFAULT_PAGE
  const pageSize = Math.min(query.page_size ?? DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE)
  return http.get<ComplaintListResponse, ComplaintListResponse>('/api/v1/complaints', {
    params: {
      page,
      page_size: pageSize,
      // task_status 为 FastAPI list[str] 重复参数契约（?task_status=a&task_status=b）；
      // axios 默认数组序列化带 [] 后缀，须用 indexes:false 关闭
      ...(query.task_status?.length ? { task_status: query.task_status } : {}),
    },
    paramsSerializer: { indexes: false },
    timeout: TIMEOUT.normal,
  })
}

/** 2. 手工建单 POST /api/v1/complaints（complaints.py:66） */
export function createComplaint(body: ComplaintCreateRequest): Promise<ComplaintCreateResponse> {
  return http.post<ComplaintCreateResponse, ComplaintCreateResponse>('/api/v1/complaints', body, {
    timeout: TIMEOUT.normal,
  })
}

/** 3. 演示灌数 POST /api/v1/complaints/demo/seed（complaints.py:112） */
export function seedDemoComplaints(): Promise<DemoSeedResponse> {
  return http.post<DemoSeedResponse, DemoSeedResponse>(
    '/api/v1/complaints/demo/seed',
    undefined,
    { timeout: TIMEOUT.normal },
  )
}

/** 4. 事件列表 GET /api/v1/complaints/events（complaints.py:123） */
export function listComplaintEvents(topic?: string): Promise<ComplaintEventsResponse> {
  return http.get<ComplaintEventsResponse, ComplaintEventsResponse>(
    '/api/v1/complaints/events',
    { params: topic ? { topic } : {}, timeout: TIMEOUT.normal },
  )
}

/** 5. 投诉详情 GET /api/v1/complaints/{id}（complaints.py:136） */
export function getComplaint(complaintId: string): Promise<ComplaintDetailResponse> {
  return http.get<ComplaintDetailResponse, ComplaintDetailResponse>(
    `/api/v1/complaints/${complaintId}`,
    { timeout: TIMEOUT.normal },
  )
}

/** 6. 研判触发 POST /api/v1/complaints/{id}/judge（complaints.py:174，长请求 110s）。
 *  signal 用于页面卸载时取消在途请求（SSOT §14 长任务取消）；不传时 config 与旧版一致。 */
export function judgeComplaint(
  complaintId: string,
  body: JudgeRequest = {},
  signal?: AbortSignal,
): Promise<JudgeResponse> {
  return http.post<JudgeResponse, JudgeResponse>(
    `/api/v1/complaints/${complaintId}/judge`,
    body,
    { timeout: TIMEOUT.judge, ...(signal ? { signal } : {}) },
  )
}

/** 7. 研判状态 GET /api/v1/complaints/{id}/status（complaints.py:287，轮询用） */
export function getComplaintStatus(complaintId: string): Promise<StatusResponse> {
  return http.get<StatusResponse, StatusResponse>(
    `/api/v1/complaints/${complaintId}/status`,
    { timeout: TIMEOUT.normal },
  )
}

/** 8. 研判结论 GET /api/v1/complaints/{id}/verdict（complaints.py:297） */
export function getComplaintVerdict(complaintId: string): Promise<VerdictResponse> {
  return http.get<VerdictResponse, VerdictResponse>(
    `/api/v1/complaints/${complaintId}/verdict`,
    { timeout: TIMEOUT.normal },
  )
}

/** 9. agent 日志 GET /api/v1/complaints/{id}/agent-logs（complaints.py:332） */
export function getComplaintAgentLogs(complaintId: string): Promise<AgentLogsResponse> {
  return http.get<AgentLogsResponse, AgentLogsResponse>(
    `/api/v1/complaints/${complaintId}/agent-logs`,
    { timeout: TIMEOUT.normal },
  )
}

/** 10. 全链路 trace GET /api/v1/complaints/{id}/trace（complaints.py:342） */
export function getComplaintTrace(complaintId: string): Promise<TraceResponse> {
  return http.get<TraceResponse, TraceResponse>(
    `/api/v1/complaints/${complaintId}/trace`,
    { timeout: TIMEOUT.normal },
  )
}

/** 11. 处置结论确认 PUT /api/v1/complaints/{id}/disposition/verdict（complaints.py:358） */
export function submitDispositionVerdict(
  complaintId: string,
  body: DispositionVerdictRequest,
): Promise<DispositionVerdictResponse> {
  return http.put<DispositionVerdictResponse, DispositionVerdictResponse>(
    `/api/v1/complaints/${complaintId}/disposition/verdict`,
    body,
    { timeout: TIMEOUT.normal },
  )
}

/** 12. 处置回调登记 PUT /api/v1/complaints/{id}/disposition/callback（complaints.py:387） */
export function submitDispositionCallback(
  complaintId: string,
  body: DispositionCallbackRequest = {},
): Promise<DispositionCallbackResponse> {
  return http.put<DispositionCallbackResponse, DispositionCallbackResponse>(
    `/api/v1/complaints/${complaintId}/disposition/callback`,
    body,
    { timeout: TIMEOUT.normal },
  )
}

/** 13. 人工复核 PUT /api/v1/complaints/{id}/review（complaints.py:417） */
export function reviewComplaint(
  complaintId: string,
  body: ReviewRequest,
): Promise<ReviewResponse> {
  return http.put<ReviewResponse, ReviewResponse>(
    `/api/v1/complaints/${complaintId}/review`,
    body,
    { timeout: TIMEOUT.normal },
  )
}

/** 14. 关闭工单 PUT /api/v1/complaints/{id}/close（complaints.py:437） */
export function closeComplaint(
  complaintId: string,
  body: CloseRequest = {},
): Promise<CloseResponse> {
  return http.put<CloseResponse, CloseResponse>(
    `/api/v1/complaints/${complaintId}/close`,
    body,
    { timeout: TIMEOUT.normal },
  )
}

/** 15. 投诉统计 GET /api/v1/complaints/stats（stats.py:18） */
export function getComplaintStats(): Promise<StatsResponse> {
  return http.get<StatsResponse, StatsResponse>('/api/v1/complaints/stats', {
    timeout: TIMEOUT.normal,
  })
}

/** 16. 运行模式切换 PUT /api/v1/complaints/mode（mode.py:26，双签） */
export function switchComplaintMode(body: ModeSwitchRequest): Promise<ModeSwitchResponse> {
  return http.put<ModeSwitchResponse, ModeSwitchResponse>('/api/v1/complaints/mode', body, {
    timeout: TIMEOUT.normal,
  })
}
