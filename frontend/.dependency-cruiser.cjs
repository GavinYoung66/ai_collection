/** @type {import('dependency-cruiser').IConfiguration} */
// 分层边界（SSOT §7）：app → modules → shared，单向依赖。
// 本配置是「架构铁律」的自动守门员，任何越界都会让 `npx depcruise src` 非 0 退出。
module.exports = {
  forbidden: [
    // 1. 模块之间不得互相依赖：modules/A ─×→ modules/B
    //    跨模块复用必须先下沉到 shared/；$1 引用 from.path 捕获的模块名。
    {
      name: 'no-cross-module-import',
      comment: '模块之间不得互相 import；跨模块复用请下沉到 shared/。',
      from: { path: '^src/modules/([^/]+)/' },
      to: { path: '^src/modules/[^/]+/', pathNot: '^src/modules/$1/' },
      severity: 'error',
    },

    // 2. shared 是最底层，不得反向依赖 modules
    {
      name: 'no-shared-to-modules',
      comment: 'shared/ 是最底层，禁止依赖 modules/。',
      from: { path: '^src/shared/' },
      to: { path: '^src/modules/' },
      severity: 'error',
    },

    // 2-bis. shared 不得依赖 app（app 是装配层，只出不进）
    //        spec R1 Scenario 明确要求 depcruise 报「无 shared → app 违规」，
    //        缺此规则时该铁律永远无法被检出，故单独立一条。
    {
      name: 'no-shared-to-app',
      comment: 'shared/ 禁止依赖 app/；依赖 app 会形成 shared ← app 的环。',
      from: { path: '^src/shared/' },
      to: { path: '^src/app/' },
      severity: 'error',
    },

    // 3. modules 不得依赖 app（app 是装配层，只出不进）
    {
      name: 'no-modules-to-app',
      comment: 'modules/ 禁止依赖 app/；app 是唯一装配层。',
      from: { path: '^src/modules/' },
      to: { path: '^src/app/' },
      severity: 'error',
    },

    // 4. mocks/ 只能由 app/main.ts 在 DEV 条件引入，其它文件不得引用
    {
      name: 'mocks-only-from-main',
      comment: 'mocks/ 仅允许 app/main.ts 在 DEV 下条件引入。',
      from: { pathNot: '^src/app/main\\.ts$' },
      to: { path: '^src/mocks/' },
      severity: 'error',
    },

    // 5. modules 只能经 barrel 使用 shared，禁止深链到具体文件（SSOT §19.7）
    //    例外仅 `shared/request.ts`：api 单测按该路径 vi.mock，走 barrel 会使 mock 失效。
    //    例外登记在此，属「有意豁免」，不是遗漏——新增豁免必须同步更新前端架构.md §19.7。
    {
      name: 'no-modules-deep-import-shared',
      comment: 'modules/ 只能从 @/shared 导入；唯一例外 @/shared/request（单测 mock 依赖路径）。',
      from: { path: '^src/modules/' },
      to: {
        path: '^src/shared/',
        pathNot: '^src/shared/(index\\.ts|request\\.ts)$',
      },
      severity: 'error',
    },
  ],

  options: {
    // 用 tsconfig.app.json 的 paths 解析 @ 别名
    tsConfig: { fileName: 'tsconfig.app.json' },
    // .vue 已在 depcruise 默认扩展名内，无需额外声明
  },
}
