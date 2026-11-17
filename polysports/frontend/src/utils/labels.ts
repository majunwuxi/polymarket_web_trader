export const resultBadge = (won: any) =>
  won === true ? { cls: 'result-win', text: '选项获胜' }
    : won === false ? { cls: 'result-loss', text: '选项失败' } : null

export const gameStatusName = (status: string) =>
  ({ scheduled: '未开始', live: '进行中', finished: '已完赛', postponed: '已延期', cancelled: '已取消', interrupted: '已中断', unknown: '状态未知' } as Record<string, string>)[status] || '状态未知'

export const gameBindingName = (state: string | null) =>
  ({ bound: '赛果已绑定', pending: '赛果匹配中', needs_review: '赛果待确认' } as Record<string, string>)[state || ''] || '赛果未绑定'

export const orderStateName = (state: string) =>
  ({ submitting: '提交中', pending: '已接受，待核对', partially_filled: '部分成交', filled: '全部成交', rejected: '已拒绝', cancelled: '已撤单', unknown: '结果未知' } as Record<string, string>)[state] || '状态未知'

export const cancelStateName = (state: string) =>
  ({ submitting: '撤单中', unknown: '撤单结果未知', confirmed: '已确认撤单', rejected: '撤单被拒绝' } as Record<string, string>)[state] || '未知'

export const comboStateName = (state: string) =>
  ({ requesting: '询价中', quoted: '已报价，未确认成交', accepting: '确认中', executing: '执行中，未确认成交', confirmed: '已确认', expired: '报价已过期', rejected: '已拒绝', failed: '未成交', unknown: '结果未知' } as Record<string, string>)[state] || '状态未知'

export const watchStateName = (state: string | null) =>
  ({ active: '已关注', completed: '已完赛，停止查询' } as Record<string, string>)[state || ''] || '未关注'

export const orderBindingName = (state: string | null) =>
  ({ pending: '等待匹配', needs_review: '待确认绑定', bound: '已绑定', failed: '绑定失败' } as Record<string, string>)[state || ''] || '未绑定'

export const tradeErrorName = (state: string) =>
  ({ engine_restarting: '撮合引擎正在重启，系统未自动重发', post_only_mode: '市场处于 post-only 模式，系统未自动改变或重发订单', signed_order_mismatch: '签名参数不一致，订单未发送' } as Record<string, string>)[state] || state

export const legWinner = (won: any, fallback: string) =>
  won === true ? '选项获胜' : won === false ? '选项未获胜' : fallback

export const comboWinner = (won: any) =>
  won === true ? '全部腿命中，组合获胜' : won === false ? '已有腿未命中，组合未获胜' : '暂不能确认组合胜负'

export const comboResult = (won: any) => won === true ? '获胜' : won === false ? '未获胜' : '未确认'

export const comboAgreement = (agreement: string) =>
  ({ consistent: '一致，组合胜负已确认', conflict: '不一致，请人工核实，不确认组合胜负', market_only: '仅有各腿市场终态，按市场结果核对，建议人工核实', result_only: '仅有赛果判断，市场终态未齐，组合结果仅为预计值', unavailable: '两个结果均不可用，暂不能确认', not_applicable: '组合卖出不适用终态胜负核对' } as Record<string, string>)[agreement] || '暂不能确认'

export const legCheck = (agreement: string) =>
  ({ consistent: '两个结果一致', conflict: '两个结果不一致，需人工核实', partial: '仅有一个结果，暂不确认', unavailable: '两个结果均不可用' } as Record<string, string>)[agreement] || '暂不确认'

export const comboBuySource = (combo: any) =>
  ({ combo_fill: '已落库 CONFIRMED 成交明细（签名接受订单的 makerAmount / takerAmount）', quote_rebuilt: '官方签名报价按 micro-USDC 精度重建', quote_partial: '签名报价字段不完整，金额未核实', unavailable: '暂无可核实的成交明细或报价' } as Record<string, string>)[combo?.buy_summary?.basis] || '暂无可核实的成交明细或报价'
