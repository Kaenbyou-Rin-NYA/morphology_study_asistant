import { useCallback, useEffect, useRef, useState } from 'react'
import OpenSeadragon from 'openseadragon'
import { BookOpen, Check, ChevronRight, CircleHelp, Crosshair, FileUp, Focus, Layers, Minus, MousePointer2, Plus, RotateCcw, ScanSearch, X } from 'lucide-react'

type Slide = { id: string; name: string; status: string; demo: boolean; width?: number; height?: number; mpp?: string; error?: string }
type Target = { name: string; bbox: number[]; polygon?: number[][] | null; annotation: string; source: string; explanation: string; confusion: string }
type Session = { id: string; phase: string; goal: string; target: Target }
type Result = { correct: boolean | null; result: 'correct' | 'incorrect' | 'pending' | 'uncertain'; x: number; y: number; elapsed: number; index: number; reason: string }
type Review = { accuracy: number | null; count: number; scored: number; uncertain: number; correct: number; elapsed: number; bbox: number[]; note: string; reinforcement: string }
type Analysis = { id: string; status: string; progress: number; candidates: { id: string }[]; error?: string }
type Configuration = { max_upload_bytes: number; classes: { id: string; name: string }[]; models: Record<string, { configured: boolean; message: string }> }
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

function Viewer({ slide, phase, target, results, onAnswer, answerMode, onAnswerModeChange, onReady }: {
  slide: Slide | undefined; phase: Phase; target?: Target; onAnswer: (x: number, y: number) => void
  answerMode: boolean; onAnswerModeChange: (value: boolean) => void; onReady: (viewer: OpenSeadragon.Viewer | null) => void
  results: Result[]
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
      if (current.current.phase !== 'student' || !current.current.answerMode || !event.position || (event as unknown as { quick?: boolean }).quick === false) return
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
    const label = document.createElement('span')
    label.textContent = target.polygon ? '模型轮廓 · 暂定' : target.annotation.includes('模型') ? '模型候选区域 · 暂定' : '演示框选区域'
    box.append(label)
    if (target.polygon?.length) {
      box.classList.add('has-polygon')
      const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
      svg.setAttribute('viewBox', `0 0 ${x2-x1} ${y2-y1}`)
      svg.setAttribute('preserveAspectRatio', 'none')
      const shape = document.createElementNS('http://www.w3.org/2000/svg', 'polygon')
      shape.setAttribute('points', target.polygon.map(([x, y]) => `${x-x1},${y-y1}`).join(' '))
      svg.append(shape); box.append(svg)
    }
    const add = () => osd.addOverlay({ element: box, location: osd.viewport.imageToViewportRectangle(x1, y1, x2 - x1, y2 - y1) })
    if (osd.world.getItemCount()) add()
    else osd.addOnceHandler('open', add)
    return () => { osd.removeHandler('open', add); if (viewer.current === osd) osd.removeOverlay(box) }
  }, [target, phase, slide])

  useEffect(() => {
    const osd = viewer.current
    if (!osd || (phase !== 'student' && phase !== 'review')) return
    const markers: HTMLElement[] = []
    const add = () => results.forEach(result => {
      const marker = document.createElement('div')
      marker.className = `click-marker ${result.result}`
      marker.textContent = String(result.index)
      marker.title = result.reason
      markers.push(marker)
      osd.addOverlay({ element: marker, location: osd.viewport.imageToViewportCoordinates(result.x, result.y), placement: OpenSeadragon.Placement.CENTER, checkResize: false })
    })
    if (osd.world.getItemCount()) add(); else osd.addOnceHandler('open', add)
    return () => { osd.removeHandler('open', add); if (viewer.current === osd) markers.forEach(marker => osd.removeOverlay(marker)) }
  }, [results, phase, slide?.id])

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
  const [uploadProgress, setUploadProgress] = useState(0)
  const [configuration, setConfiguration] = useState<Configuration | null>(null)
  const [task, setTask] = useState<Analysis | null>(null)
  const [error, setError] = useState('')
  const slide = slides.find(s => s.id === selected)

  const refresh = useCallback(() => api<Slide[]>('/slides').then(setSlides).catch(e => setError(e.message)), [])
  useEffect(() => { void refresh() }, [refresh])
  useEffect(() => { api<Configuration>('/config').then(setConfiguration).catch(e => setError(e.message)) }, [])
  useEffect(() => {
    setTask(null)
  }, [selected, goal])
  useEffect(() => {
    if (!task || !['queued', 'processing'].includes(task.status)) return
    const timer = window.setInterval(() => {
      api<Analysis>(`/analyses/${task.id}`).then(setTask).catch(e => setError(e.message))
    }, 1500)
    return () => window.clearInterval(timer)
  }, [task?.id, task?.status])
  useEffect(() => {
    if (!session || !results.some(r => r.result === 'pending')) return
    const timer = window.setInterval(() => {
      api<Result[]>(`/sessions/${session.id}/answers`).then(setResults).catch(e => setError(e.message))
    }, 1500)
    return () => window.clearInterval(timer)
  }, [session?.id, results])
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
      const data = await api<Session>('/sessions', jsonPost({ slide_id: selected, goal, analysis_id: task?.status === 'completed' ? task.id : undefined }))
      setSession(data); setResults([]); setReview(null); setPhase('teacher')
      window.setTimeout(() => focusBox(data.target.bbox), 350)
    } catch (e) { setError((e as Error).message) } finally { setBusy(false) }
  }

  async function analyze() {
    setBusy(true); setError('')
    try { setTask(await api<Analysis>(`/slides/${selected}/analyses`, jsonPost({ goal }))) }
    catch (e) { setError((e as Error).message) } finally { setBusy(false) }
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
    if (configuration && file.size > configuration.max_upload_bytes) { setError(`文件超过 ${configuration.max_upload_bytes / 1024**3} GiB 上限`); return }
    setUploading(true); setUploadProgress(0); setError('')
    try {
      const key = `morph-upload:${file.name}:${file.size}:${file.lastModified}`
      const remembered = localStorage.getItem(key)
      let job: { id: string; offset: number; size: number }
      if (remembered) {
        try { job = await api(`/uploads/${remembered}`) }
        catch { localStorage.removeItem(key); job = await api('/uploads', jsonPost({ filename: file.name, size: file.size })) }
      } else job = await api('/uploads', jsonPost({ filename: file.name, size: file.size }))
      if (job.size !== file.size) throw new Error('续传任务的文件大小与本地文件不匹配')
      if ('status' in job && (job as { status?: string }).status !== 'uploading') {
        localStorage.removeItem(key)
        job = await api('/uploads', jsonPost({ filename: file.name, size: file.size }))
      }
      localStorage.setItem(key, job.id)
      const chunkSize = 4 * 1024 * 1024
      while (job.offset < file.size) {
        const end = Math.min(job.offset + chunkSize, file.size)
        const response = await fetch(`/api/uploads/${job.id}/chunk`, {
          method: 'PUT', headers: { 'Content-Range': `bytes ${job.offset}-${end-1}/${file.size}` }, body: file.slice(job.offset, end),
        })
        if (!response.ok) {
          const detail = await response.json().catch(() => ({}))
          throw new Error(detail.detail || `上传失败 (${response.status})`)
        }
        job = await response.json()
        setUploadProgress(Math.round(job.offset / file.size * 100))
      }
      const item = await api<Slide>(`/uploads/${job.id}/complete`, jsonPost())
      localStorage.removeItem(key)
      setSlides(old => [...old, item]); setSelected(item.id); reset()
    } catch (e) { setError((e as Error).message) } finally { setUploading(false) }
  }

  function reset() { setSession(null); setPhase('prepare'); setResults([]); setReview(null); setAnswerMode(false); setTask(null); setError('') }
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
            <label className="upload-button"><FileUp size={16} /> {uploading ? `正在上传 · ${uploadProgress}%` : '上传本地切片'}<input type="file" accept=".svs,.tif,.tiff,.ndpi,.mrxs" hidden disabled={uploading} onChange={e => { void upload(e.target.files?.[0]); e.target.value = '' }} /></label>
            {uploading && <progress className="upload-progress" max={100} value={uploadProgress} />}
            <div className="divider" />
            <div className="section-label"><ScanSearch size={15} /> 学习目标</div>
            <label className="input-label" htmlFor="goal">想在这张切片中学习什么？</label>
            <textarea id="goal" value={goal} onChange={e => setGoal(e.target.value)} rows={4} placeholder="例如：认识主动脉组织区域" />
            <p className="field-hint">主动脉演示无需模型；其他目标需先分析切片。上传上限 {configuration ? `${(configuration.max_upload_bytes / 1024**3).toFixed(0)} GiB` : '读取中'}。</p>
            {configuration && <div className="model-info">模型状态：CONCH {configuration.models.conch.configured ? '已配置' : '未配置权重'} · InstanSeg {configuration.models.instanseg.configured ? '已配置' : '未配置'}</div>}
            {task && <div className="task-info">分析{task.status === 'failed' ? '失败' : task.status === 'completed' ? '完成' : '进行中'} · {task.progress}% · {task.candidates.length} 个候选{task.error ? ` · ${task.error}` : ''}</div>}
            <button className="mode-button" disabled={busy || slide?.status !== 'ready' || !goal.trim() || task?.status === 'processing' || task?.status === 'queued'} onClick={() => void analyze()}><ScanSearch size={16} /> 分析切片 · 模型候选</button>
            <button className="primary-button" disabled={busy || slide?.status !== 'ready' || !goal.trim() || !!task && task.status !== 'completed'} onClick={() => void start()}><BookOpen size={17} /> {busy ? '准备中…' : '开始导览'} <ChevronRight size={16} /></button>
          </>}
          {phase === 'teacher' && session && <>
            <div className="section-label"><BookOpen size={15} /> 导览讲解</div><div className="target-tag">01 / 目标结构</div><h2>{session.target.name}</h2>
            <p className="body-copy">{session.target.explanation}</p><div className="note"><CircleHelp size={16} /><span>{session.target.confusion}</span></div>
            <p className="source">标注状态：{session.target.annotation}<br />来源：{session.target.source}</p>
            <button className="primary-button" disabled={busy} onClick={() => void beginExam()}><MousePointer2 size={17} /> 进入独立自测 <ChevronRight size={16} /></button>
          </>}
          {phase === 'student' && <>
            <div className="section-label"><Crosshair size={15} /> 独立自测</div><h2>找到{session?.target.name || '目标区域'}</h2><p className="body-copy">切片已返回全图。找到目标后右键点击；历史位置以绿、红、灰分别标记正确、错误与待判定。</p>
            <button className={`mode-button ${answerMode ? 'on' : ''}`} onClick={() => setAnswerMode(!answerMode)}><Crosshair size={16} /> {answerMode ? '定位模式已开启 · 点击切片' : '触屏 / 触控板：定位作答'}</button>
            <div className="attempt-header"><span>作答记录</span><span>{results.length} 次</span></div>
            {results.length === 0 ? <p className="empty-attempts">尚未作答。可平移、放大切片后选择位置。</p> : <div className="attempt-list">{results.map(r => <div className="attempt" key={r.index}><span className={r.result === 'correct' ? 'result-good' : r.result === 'incorrect' ? 'result-bad' : 'result-unknown'}>{r.result === 'correct' ? <Check size={15} /> : r.result === 'incorrect' ? <X size={15} /> : <CircleHelp size={15} />}</span><div><strong>第 {r.index} 次 · {r.result === 'correct' ? '命中' : r.result === 'incorrect' ? '未命中' : r.result === 'pending' ? '判定中' : '无法判定'}</strong><small>{r.reason}</small></div><span className="attempt-time">{r.elapsed}s</span></div>)}</div>}
            <button className="primary-button" disabled={busy || results.length === 0 || results.some(r => r.result === 'pending')} onClick={() => void finish()}><BookOpen size={17} /> 查看复习报告 <ChevronRight size={16} /></button>
          </>}
          {phase === 'review' && review && <>
            <div className="section-label"><BookOpen size={15} /> 学习报告</div><h2>本次定位练习</h2>
            <div className="stats"><div><strong>{review.accuracy === null ? '—' : `${review.accuracy}%`}</strong><span>已判题命中率</span></div><div><strong>{review.correct}/{review.scored}</strong><span>正确 / 已判定</span></div><div><strong>{review.uncertain}</strong><span>无法判定</span></div></div>
            <div className="review-label">复习要点</div><p className="body-copy">{review.reinforcement}</p><div className="note"><CircleHelp size={16} /><span>{review.note}</span></div>
            <button className="primary-button" onClick={reset}><RotateCcw size={17} /> 开始新一轮 <ChevronRight size={16} /></button>
          </>}
          {error && <div className="error" role="alert">{error}</div>}
        </div>
        <div className="sidebar-footer"><span className="mini-dot" /> 本地演示环境 <span>·</span> 数据保存在本机 SQLite</div>
      </aside>
      <main className="main">
        <div className="viewer-toolbar"><div className="toolbar-title"><span className="eyebrow">SLIDE VIEWER</span><strong>{slide?.name || '未选择切片'}</strong><small>{phase === 'teacher' ? '导览模式' : phase === 'student' ? '自测模式' : phase === 'review' ? '复习模式' : '全切片视图'}</small></div><div className="toolbar-actions"><button title="缩小" aria-label="缩小" onClick={() => viewer?.viewport.zoomBy(0.7)}><Minus size={18} /></button><button title="适配全图" aria-label="适配全图" onClick={() => viewer?.viewport.goHome()}><Focus size={18} /></button><button title="放大" aria-label="放大" onClick={() => viewer?.viewport.zoomBy(1.4)}><Plus size={18} /></button></div></div>
        <div className="canvas-wrap">{slide?.status === 'ready' ? <Viewer slide={slide} phase={phase} target={session?.target} results={results} onAnswer={(x, y) => void answer(x, y)} answerMode={answerMode} onAnswerModeChange={setAnswerMode} onReady={setViewer} /> : <div className="canvas-empty"><Layers size={36} /><strong>{slide?.status === 'processing' ? '切片处理中' : '等待切片加载'}</strong><span>{slide?.status === 'processing' ? '读取完成后将自动显示' : '从左侧选择或上传一张切片'}</span></div>}
          {slide?.status === 'ready' && <div className="canvas-badge"><span className="mini-dot" /> {phase === 'student' ? '答案区域已隐藏 · 显示历史点击' : phase === 'teacher' || phase === 'review' ? session?.target.annotation : 'WSI 瓦片按需加载'}</div>}
        </div>
        <div className="viewer-footer"><span><ScanSearch size={14} /> OpenSlide / Deep Zoom</span><span>{slide?.mpp ? `${Number(slide.mpp).toFixed(3)} μm/px` : '倍率信息未提供'} <span className="footer-divider">|</span> 滚轮缩放 · 拖动平移</span></div>
      </main>
    </div>
  </div>
}
