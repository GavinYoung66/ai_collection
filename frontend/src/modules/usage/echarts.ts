/**
 * echarts 按需注册（SSOT:466 授权仅本模块引入；禁止整包 import）。
 * 只注册当前图表所需：LineChart / PieChart + Grid / Tooltip / Legend + CanvasRenderer。
 * 批次⑤ 的三个图表组件统一从此处取 echarts 实例。
 */
import * as echarts from 'echarts/core'
import { LineChart, PieChart } from 'echarts/charts'
import {
  GridComponent,
  LegendComponent,
  TooltipComponent,
} from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'

echarts.use([
  LineChart,
  PieChart,
  GridComponent,
  TooltipComponent,
  LegendComponent,
  CanvasRenderer,
])

export default echarts
