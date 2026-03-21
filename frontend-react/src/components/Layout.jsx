import { useState, useEffect } from 'react';
import { Outlet } from 'react-router-dom';
import Sidebar from '@/components/Sidebar';
import Header from '@/components/Header';
import ActivityFeed from '@/components/ActivityFeed';

export default function Layout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const [isLg, setIsLg] = useState(false);

  useEffect(() => {
    function check() {
      setIsLg(window.innerWidth >= 1024);
    }
    check();
    window.addEventListener('resize', check);
    return () => window.removeEventListener('resize', check);
  }, []);

  return (
    <div className="flex h-screen overflow-hidden bg-background">
      {/* Sidebar */}
      <div className="hidden lg:block">
        <Sidebar
          collapsed={sidebarCollapsed}
          onToggle={() => setSidebarCollapsed(!sidebarCollapsed)}
        />
      </div>

      {/* Mobile sidebar overlay */}
      {mobileMenuOpen && (
        <>
          <div
            className="fixed inset-0 z-30 bg-black/50 lg:hidden"
            onClick={() => setMobileMenuOpen(false)}
          />
          <div className="fixed left-0 top-0 bottom-0 z-40 lg:hidden">
            <Sidebar
              collapsed={false}
              onToggle={() => setMobileMenuOpen(false)}
            />
          </div>
        </>
      )}

      {/* Main content area */}
      <div
        className="flex flex-1 flex-col overflow-hidden transition-all duration-200"
        style={{
          marginLeft: isLg ? (sidebarCollapsed ? 72 : 256) : 0,
        }}
      >
        <Header onMenuToggle={() => setMobileMenuOpen(!mobileMenuOpen)} />

        <div className="flex flex-1 overflow-hidden">
          {/* Page content */}
          <main className="flex-1 overflow-y-auto scrollbar-thin p-6">
            <Outlet />
          </main>

          {/* Activity feed - right sidebar */}
          <aside className="hidden xl:flex w-80 flex-col border-l border-border bg-surface/50">
            <ActivityFeed />
          </aside>
        </div>
      </div>
    </div>
  );
}
