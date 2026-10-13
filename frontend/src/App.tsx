import { useCallback, useEffect, useRef, useState } from 'react'
import OpenSeadragon from 'openseadragon'
import { BookOpen, Check, ChevronRight, CircleHelp, Crosshair, FileUp, Focus, Layers, Minus, MousePointer2, Plus, RotateCcw, ScanSearch, X } from 'lucide-react'

type Slide = { id: string; name: string; status: string; demo: boolean; width?: number; height?: number; mpp?: string; error?: string }
type Target = { name: string; bbox: number[]; annotation: string; source: string; explanation: string; confusion: string }
type Session = { id: string; phase: string; goal: string; target: Target }
type Result = { correct: boolean; elapsed: number; index: number; reason: string }
type Review = { accuracy: number; count: number; correct: number; elapsed: number; bbox: number[]; note: string; reinforcement: string }
type Phase = 'prepare' | 'teacher' | 'student' | 'review'

async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch('/api' + path, options)
  if (!res.ok) {
    const data = await res.json().catch(() => ({}))
    throw new Error(typeof data.detail === 'string' ? data.detail : `请求失败 (${res.status})`)
  }
  return res.json()
}

const jsonPost = (body?: object): RequestInit => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body ?? {}) })

function Viewer({ slide, phase, target, onAnswer, answerMode, onAnswerModeChange, onReady }: {
  slide: Slide | undefined; phase: Phase; target?: Target; onAnswer: (x: number, y: number) => void
  answerMode: boolean; onAnswerModeChange: (value: boolean) => void; onReady: (viewer: OpenSeadragon.Viewer | null) => void
}) {
  const element = useRef<HTMLDivElement>(null)
  const viewer = useRef<OpenSeadragon.Viewer | null>(null)
  const current = useRef({ phase, answerMode, onAnswer })
  current.current = { phase, answerMode, onAnswer }

  useEffect(() => {
    if (!element.current || !slide || slide.status !== 'ready') return
    const osd = OpenSeadragon({
      element: element.current, tileSources: `/api/slides/${slide.id}/image.dzi`,
      showNavigationControl: false, showNavigator: true, navigatorPosition: 'BOTTOM_RIGHT',
      gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true, scrollToZoom: true, pinchToZoom: true, flickEnabled: true },
      maxZoomPixelRatio: 3, animationTime: 0.65, visibilityRatio: 0.7,
    })
    viewer.current = osd
    onReady(osd)
    const handleContext = (event: MouseEvent) => {
      event.preventDefault()
      if (current.current.phase !== 'student') return
      const rect = element.current!.getBoundingClientRect()
      const point = osd.viewport.pointFromPixel(new OpenSeadragon.Point(event.clientX - rect.left, event.clientY - rect.top))
      const image = osd.viewport.viewportToImageCoordinates(point)
      current.current.onAnswer(image.x, image.y)
    }
    const handleClick = (event: OpenSeadragon.ViewerEvent & { position?: OpenSeadragon.Point }) => {
      if (current.current.phase !== 'student' || !current.current.answerMode || !event.position) return
      const image = osd.viewport.viewportToImageCoordinates(osd.viewport.pointFromPixel(event.position))
      current.current.onAnswer(image.x, image.y)
      current.current.answerMode = false
      onAnswerModeChange(false)
    }
    element.current.addEventListener('contextmenu', handleContext)
    osd.addHandler('canvas-click', handleClick)
    return () => {
      element.current?.removeEventListener('contextmenu', handleContext)
      osd.destroy()
      viewer.current = null
      onReady(null)
    }
  }, [slide?.id, slide?.status])

  useEffect(() => {
    if (!viewer.current || !slide || !target || phase === 'student' || phase === 'prepare') return
    const osd = viewer.current
    const [x1, y1, x2, y2] = target.bbox
    const box = document.createElement('div')
    box.className = 'annotation-box'
    box.innerHTML = '<span>演示框选区域</span>'
    const add = () => osd.addOverlay({ element: box, location: osd.viewport.imageToViewportRectangle(x1, y1, x2 - x1, y2 - y1) })
    if (osd.world.getItemCount()) add()
    else osd.addOnceHandler('open', add)
    return () => { if (viewer.current === osd) osd.removeOverlay(box) }
  }, [target, phase, slide])

  return <div className={`viewer ${answerMode ? 'viewer-answer' : ''}`} ref={element} />
}

export default function App() {
  const [slides, setSlides] = useState<Slide[]>([])
  const [selected, setSelected] = useState('aorta-demo')
  const [goal, setGoal] = useState('认识主动脉组织区域')
  const [phase, setPhase] = useState<Phase>('prepare')
  const [session, setSession] = useState<Session | null>(null)
  const [results, setResults] = useState<Result[]>([])
  const [review, setReview] = useState<Review | null>(null)
  const [viewer, setViewer] = useState<OpenSeadragon.Viewer | null>(null)
  const [answerMode, setAnswerMode] = useState(false)
  const [busy, setBusy] = useState(false)
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState('')
  const slide = slides.find(s => s.id === selected)

  const refresh = useCallback(() => api<Slide[]>('/slides').then(setSlides).catch(e => setError(e.message)), [])
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => {
    if (!slides.some(s => s.status === 'processing')) return
    const timer = window.setInterval(() => void refresh(), 2500)
    return () => window.clearInterval(timer)
  }, [slides, refresh])

  function focusBox(box: number[]) {
    if (!viewer) return
    const [x1, y1, x2, y2] = box
    const focus = () => viewer.viewport.fitBoundsWithConstraints(viewer.viewport.imageToViewportRectangle(x1, y1, x2 - x1, y2 - y1), false)
    if (viewer.world.getItemCount()) focus()
    else viewer.addOnceHandler('open', focus)
  }

  async function start() {
    setBusy(true); setError('')
    try {
      const data = await api<Session>('/sessions', jsonPost({ slide_id: selected, goal }))
      setSession(data); setResults([]); setReview(null); setPhase('teacher')
      window.setTimeout(() => focusBox(data.target.bbox), 350)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  async function beginExam() {
    if (!session) return
    setBusy(true); setError('')
    try {
      await api(`/sessions/${session.id}/exam`, jsonPost())
      setPhase('student'); setAnswerMode(false); viewer?.viewport.goHome(false)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  const answer = useCallback(async (x: number, y: number) => {
    if (!session || busy) return
    setBusy(true); setError('')
    try {
      const result = await api<Result>(`/sessions/${session.id}/answers`, jsonPost({ x, y }))
      setResults(old => [...old, result])
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }, [session, busy])

  async function finish() {
    if (!session) return
    setBusy(true); setError('')
    try {
      const data = await api<Review>(`/sessions/${session.id}/review`, jsonPost())
      setReview(data); setPhase('review')
      window.setTimeout(() => focusBox(data.bbox), 150)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  async function upload(file?: File) {
    if (!file) return
    setUploading(true); setError('')
    try {
      const body = new FormData(); body.append('file', file)
      const item = await api<Slide>('/slides/upload', { method: 'POST', body })
      setSlides(old => [...old, item]); setSelected(item.id); reset()
    } catch (e) { setError((e as Error).message) } finally { setUploading(false) }
  }

  function reset() { setSession(null); setPhase('prepare'); setResults([]); setReview(null); setAnswerMode(false); setError('') }
  const stageIndex = { prepare: 0, teacher: 1, student: 2, review: 3 }[phase]

  return <div className="app">
    <header className="topbar">
      <div className="brand"><div className="brand-mark"><Layers size={20} strokeWidth={2.1} /></div><div><strong>形态学学习台</strong><small>MORPHOLOGY STUDIO</small></div></div>
      <div className="header-middle"><span className="status-dot" /> 组织学 · 切片观察实验台 <span className="header-separator">/</span> 初版演示</div>
      <span className="version">V 0.1</span>
    </header>
    <div className="workspace">
      <aside className="sidebar">
        <div className="sidebar-header"><span className="eyebrow">STUDY SESSION</span><h1>学习工作区</h1><p>从全切片观察到定位练习</p></div>
        <div className="steps">{['选择切片', '导览学习', '独立自测', '复习报告'].map((name, index) => <div key={name} className={`step ${stageIndex === index ? 'active' : ''} ${stageIndex > index ? 'done' : ''}`}><span className="step-number">{stageIndex > index ? <Check size={14} /> : `0${index + 1}`}</span><span>{name}</span>{stageIndex === index && <ChevronRight size={15} />}</div>)}</div>
        <div className="sidebar-content">
          {phase === 'prepare' && <>
            <div className="section-label"><Layers size={15} /> 切片资料</div>
            <label className="input-label" htmlFor="slide">当前切片</label>
            <select id="slide" value={selected} onChange={e => { setSelected(e.target.value); setError('') }}><option value="" disabled>选择切片</option>{slides.map(s => <option key={s.id} value={s.id}>{s.name}{s.status === 'processing' ? ' · 处理中' : ''}</option>)}</select>
            <div className="slide-meta"><span className="mini-dot" />{slide?.status === 'ready' ? `${slide.width?.toLocaleString()} × ${slide.height?.toLocaleString()} px` : slide?.status === 'processing' ? '正在读取切片…' : slide?.error || '尚未加载切片'}</div>
            <label className="upload-button"><FileUp size={16} /> {uploading ? '正在上传…' : '上传本地切片'}<input type="file" accept=".svs,.tif,.tiff,.ndpi,.mrxs" hidden disabled={uploading} onChange={e => { void upload(e.target.files?.[0]); e.target.value = '' }} /></label>
            <div className="divider" />
            <div className="section-label"><ScanSearch size={15} /> 学习目标</div>
            <label className="input-label" htmlFor="goal">想在这张切片中学习什么？</label>
            <textarea id="goal" value={goal} onChange={e => setGoal(e.target.value)} rows={4} placeholder="例如：认识主动脉组织区域" />
            <p className="field-hint">当前演示支持主动脉组织区域定位。上传切片暂仅支持浏览。</p>
            <button className="primary-button" disabled={busy || slide?.status !== 'ready' || !goal.trim()} onClick={() => void start()}><BookOpen size={17} /> {busy ? '准备中…' : '开始导览'} <ChevronRight size={16} /></button>
          </>}
          {phase === 'teacher' && session && <>
            <div className="section-label"><BookOpen size={15} /> 导览讲解</div><div className="target-tag">01 / 目标结构</div><h2>{session.target.name}</h2>
            <p className="body-copy">{session.target.explanation}</p><div className="note"><CircleHelp size={16} /><span>{session.target.confusion}</span></div>
            <p className="source">标注状态：{session.target.annotation}<br />来源：{session.target.source}</p>
            <button className="primary-button" disabled={busy} onClick={() => void beginExam()}><MousePointer2 size={17} /> 进入独立自测 <ChevronRight size={16} /></button>
          </>}
          {phase === 'student' && <>
            <div className="section-label"><Crosshair size={15} /> 独立自测</div><h2>找到组织区域</h2><p className="body-copy">切片已返回全图。先自行定位刚才学习的组织区域，再在目标处右键点击。</p>
            <button className={`mode-button ${answerMode ? 'on' : ''}`} onClick={() => setAnswerMode(!answerMode)}><Crosshair size={16} /> {answerMode ? '定位模式已开启 · 点击切片' : '触屏 / 触控板：定位作答'}</button>
            <div className="attempt-header"><span>作答记录</span><span>{results.length} 次</span></div>
            {results.length === 0 ? <p className="empty-attempts">尚未作答。可平移、放大切片后选择位置。</p> : <div className="attempt-list">{results.map(r => <div className="attempt" key={r.index}><span className={r.correct ? 'result-good' : 'result-bad'}>{r.correct ? <Check size={15} /> : <X size={15} />}</span><div><strong>第 {r.index} 次 · {r.correct ? '命中' : '未命中'}</strong><small>{r.reason}</small></div><span className="attempt-time">{r.elapsed}s</span></div>)}</div>}
            <button className="primary-button" disabled={busy || results.length === 0} onClick={() => void finish()}><BookOpen size={17} /> 查看复习报告 <ChevronRight size={16} /></button>
          </>}
          {phase === 'review' && review && <>
            <div className="section-label"><BookOpen size={15} /> 学习报告</div><h2>本次定位练习</h2>
            <div className="stats"><div><strong>{review.accuracy}%</strong><span>命中率</span></div><div><strong>{review.correct}/{review.count}</strong><span>命中次数</span></div><div><strong>{review.elapsed}s</strong><span>自测耗时</span></div></div>
            <div className="review-label">复习要点</div><p className="body-copy">{review.reinforcement}</p><div className="note"><CircleHelp size={16} /><span>{review.note}</span></div>
            <button className="primary-button" onClick={reset}><RotateCcw size={17} /> 开始新一轮 <ChevronRight size={16} /></button>
          </>}
          {error && <div className="error" role="alert">{error}</div>}
        </div>
        <div className="sidebar-footer"><span className="mini-dot" /> 本地演示环境 <span>·</span> 数据仅在当前进程保存</div>
      </aside>
      <main className="main">
        <div className="viewer-toolbar"><div className="toolbar-title"><span className="eyebrow">SLIDE VIEWER</span><strong>{slide?.name || '未选择切片'}</strong><small>{phase === 'teacher' ? '导览模式' : phase === 'student' ? '自测模式' : phase === 'review' ? '复习模式' : '全切片视图'}</small></div><div className="toolbar-actions"><button title="缩小" aria-label="缩小" onClick={() => viewer?.viewport.zoomBy(0.7)}><Minus size={18} /></button><button title="适配全图" aria-label="适配全图" onClick={() => viewer?.viewport.goHome()}><Focus size={18} /></button><button title="放大" aria-label="放大" onClick={() => viewer?.viewport.zoomBy(1.4)}><Plus size={18} /></button></div></div>
        <div className="canvas-wrap">{slide?.status === 'ready' ? <Viewer slide={slide} phase={phase} target={session?.target} onAnswer={(x, y) => void answer(x, y)} answerMode={answerMode} onAnswerModeChange={setAnswerMode} onReady={setViewer} /> : <div className="canvas-empty"><Layers size={36} /><strong>{slide?.status === 'processing' ? '切片处理中' : '等待切片加载'}</strong><span>{slide?.status === 'processing' ? '读取完成后将自动显示' : '从左侧选择或上传一张切片'}</span></div>}
          {slide?.status === 'ready' && <div className="canvas-badge"><span className="mini-dot" /> {phase === 'student' ? '答案区域已隐藏' : phase === 'teacher' || phase === 'review' ? '演示框选 · 未经专家复核' : 'WSI 瓦片按需加载'}</div>}
        </div>
        <div className="viewer-footer"><span><ScanSearch size={14} /> OpenSlide / Deep Zoom</span><span>{slide?.mpp ? `${Number(slide.mpp).toFixed(3)} μm/px` : '倍率信息未提供'} <span className="footer-divider">|</span> 滚轮缩放 · 拖动平移</span></div>
      </main>
    </div>
  </div>
}
