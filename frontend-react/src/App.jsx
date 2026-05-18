import { BrowserRouter, Routes, Route } from 'react-router-dom';
import { ToastProvider } from '@/components/ui/toast';
import { SocketProvider } from '@/hooks/useSocket';
import Layout from '@/components/Layout';
import Dashboard from '@/pages/Dashboard';
import History from '@/pages/History';
import Files from '@/pages/Files';
import LibraryPage from '@/pages/LibraryPage';
import Analytics from '@/pages/Analytics';
import ReviewPage from '@/pages/ReviewPage';

export default function App() {
  return (
    <ToastProvider>
      <SocketProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="/history" element={<History />} />
              <Route path="/files" element={<Files />} />
              <Route path="/library" element={<LibraryPage />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/review" element={<ReviewPage />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </SocketProvider>
    </ToastProvider>
  );
}
