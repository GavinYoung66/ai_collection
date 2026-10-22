// mocks/ 入口（SSOT §16.1）：MSW 开发态 mock。
// 本批次仅占位：实际仅在 import.meta.env.DEV 下由 app/main.ts 动态引入；
// 后续批次在此用 setupWorker 注册 /api/v1/auth/login、/api/v1/auth/me 的假数据。
export async function setupMocks(): Promise<void> {
  // 占位：真实 MSW worker 启动逻辑在后续批次实现
}
