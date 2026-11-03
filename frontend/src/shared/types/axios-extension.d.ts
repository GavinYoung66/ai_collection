/** Axios InternalAxiosRequestConfig 扩展。
 *
 * 供 request.ts 的请求拦截器使用：当 refresh_token 端点需要自带 Bearer 头时，
 * auth.ts 的 refreshAccessToken() 在 config 中置 _skipAuthInterceptor: true，
 * 拦截器检测后跳过 authToken 注入并 delete 该标记（不下发到后端）。
 */
import 'axios'

declare module 'axios' {
  interface AxiosRequestConfig {
    _skipAuthInterceptor?: boolean
  }

  interface InternalAxiosRequestConfig {
    _skipAuthInterceptor?: boolean
  }
}
