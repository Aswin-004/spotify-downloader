import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ToastProvider } from '@/components/ui/toast';
import { SocketProvider } from '@/hooks/useSocket';
import Layout from '@/components/Layout';
import Dashboard from '@/pages/Dashboard';
import History from '@/pages/History';
import LibraryPage from '@/pages/LibraryPage';
import Analytics from '@/pages/Analytics';
import ReviewPage from '@/pages/ReviewPage';
import MaintenancePage from '@/pages/MaintenancePage';
import Settings from '@/pages/Settings';
import GettingStarted from '@/pages/GettingStarted';

export default function App() {
  return (
    <ToastProvider>
      <SocketProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="/history" element={<History />} />
              <Route path="/files"   element={<Navigate to="/library" replace />} />
              <Route path="/library" element={<LibraryPage />} />
              <Route path="/analytics" element={<Analytics />} />
              <Route path="/review" element={<ReviewPage />} />
              <Route path="/maintenance" element={<MaintenancePage />} />
              <Route path="/settings" element={<Settings />} />
              <Route path="/getting-started" element={<GettingStarted />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </SocketProvider>
    </ToastProvider>
  );
}
