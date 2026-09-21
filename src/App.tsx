import { Routes, Route } from 'react-router'
import OrderCenterPage from './pages/OrderCenterPage'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<OrderCenterPage />} />
      <Route path="/orders" element={<OrderCenterPage />} />
      <Route path="*" element={<OrderCenterPage />} />
    </Routes>
  )
}
