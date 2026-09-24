import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter } from 'react-router'
import './index.css'
import App from './App.tsx'
import { Toaster } from '@/components/ui/sonner'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <App />
      {/*
        P0 根因修复：<Toaster /> 之前只被定义、从未挂载。
        sonner 的 toast() 只是一个事件发射器，没有挂载的 Toaster 就没有订阅者，
        于是所有 toast.error / toast.success 都是**静默 no-op** ——
        用户点了「上传并写入素材库」却看不到任何反馈。
        Toaster 必须挂在路由之外，切换页面时才不会被卸载。
      */}
      <Toaster position="top-center" richColors closeButton duration={6000} />
    </BrowserRouter>
  </StrictMode>,
)
