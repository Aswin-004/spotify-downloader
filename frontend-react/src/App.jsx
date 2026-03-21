import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { SocketProvider } from '@/hooks/useSocket';
import Layout from '@/components/Layout';
import Dashboard from '@/pages/Dashboard';
import History from '@/pages/History';
import Files from '@/pages/Files';

export default function App() {
  return (
    <SocketProvider>
      <BrowserRouter>
        <Routes>
          <Route element={<Layout />}>
            <Route index element={<Dashboard />} />
            <Route path="/history" element={<History />} />
            <Route path="/files" element={<Files />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </SocketProvider>
  );
}
