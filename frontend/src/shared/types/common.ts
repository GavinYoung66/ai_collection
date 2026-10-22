/**
 * 角色枚举（唯一来源：common/db/models/user.py 的 role 字段注释）。
 * 五值逐字一致，禁止新增或改名。
 */
export type Role = 'admin' | 'supervisor' | 'agent' | 'qc_reviewer' | 'ai_trainer'

/**
 * 用户状态（来源：user.py status 字段注释）。
 */
export type UserStatus = 'active' | 'disabled' | 'locked'

/**
 * 当前登录用户（来源：/api/v1/auth/login 与 /me 的 data.user）。
 * 字段与后端逐字一致，禁止新增或改名。
 */
export interface CurrentUser {
  user_id: string
  username: string
  real_name: string
  role: Role
  status: UserStatus
}
