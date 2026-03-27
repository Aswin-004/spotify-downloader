import { createContext, useContext, useState, useCallback } from 'react';
import { motion, AnimatePresence } from 'framer-motion';
import { CheckCircle2, XCircle, SkipForward, AlertTriangle, X, Info } from 'lucide-react';
import { cn } from '@/lib/utils';

const ToastContext = createContext(null);

export function useToast() {
  return useContext(ToastContext);
}

const toastStyles = {
  success: {
    icon: CheckCircle2,
    color: 'text-emerald-400',
    border: 'border-emerald-500/30',
    bg: 'bg-emerald-500/10',
  },
  error: {
    icon: XCircle,
    color: 'text-red-400',
    border: 'border-red-500/30',
    bg: 'bg-red-500/10',
  },
  warning: {
    icon: SkipForward,
    color: 'text-amber-400',
    border: 'border-amber-500/30',
    bg: 'bg-amber-500/10',
  },
  info: {
    icon: Info,
    color: 'text-blue-400',
    border: 'border-blue-500/30',
    bg: 'bg-blue-500/10',
  },
};

function ToastItem({ id, type = 'info', title, description, duration = 4000, onDismiss }) {
  const config = toastStyles[type] || toastStyles.info;
  const Icon = config.icon;

  return (
    <motion.div
      layout
      initial={{ opacity: 0, y: 20, scale: 0.92 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, x: 100, scale: 0.92, transition: { duration: 0.2 } }}
      transition={{ duration: 0.3, ease: [0.25, 0.46, 0.45, 0.94] }}
      className={cn(
        'pointer-events-auto flex flex-col rounded-xl border shadow-2xl backdrop-blur-xl min-w-[300px] max-w-[400px] overflow-hidden',
        config.border,
        'bg-surface/95'
      )}
    >
      <div className="flex items-start gap-3 px-4 py-3">
        <motion.div
          className={cn('p-1 rounded-lg', config.bg)}
          initial={{ scale: 0, rotate: -90 }}
          animate={{ scale: 1, rotate: 0 }}
          transition={{ delay: 0.1, type: 'spring', stiffness: 500, damping: 20 }}
        >
          <Icon className={cn('w-4 h-4', config.color)} />
        </motion.div>
        <div className="flex-1 min-w-0">
          <p className="text-sm font-medium text-white">{title}</p>
          {description && (
            <p className="text-xs text-gray-400 mt-0.5 truncate">{description}</p>
          )}
        </div>
        <button
          onClick={() => onDismiss(id)}
          className="text-gray-500 hover:text-white transition-colors flex-shrink-0 mt-0.5"
        >
          <X className="w-3.5 h-3.5" />
        </button>
      </div>
      {/* Auto-dismiss progress bar */}
      <motion.div
        className={cn('h-0.5', config.bg.replace('/10', '/30'))}
        initial={{ width: '100%' }}
        animate={{ width: '0%' }}
        transition={{ duration: duration / 1000, ease: 'linear' }}
      />
    </motion.div>
  );
}

export function ToastProvider({ children }) {
  const [toasts, setToasts] = useState([]);

  const addToast = useCallback(({ type, title, description, duration = 4000 }) => {
    const id = Date.now() + Math.random();
    setToasts((prev) => [...prev.slice(-4), { id, type, title, description, duration }]);
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id));
    }, duration);
  }, []);

  const removeToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  return (
    <ToastContext.Provider value={{ addToast }}>
      {children}
      <div className="fixed bottom-4 right-4 z-[9999] flex flex-col-reverse gap-2 pointer-events-none">
        <AnimatePresence mode="popLayout">
          {toasts.map((toast) => (
            <ToastItem key={toast.id} {...toast} onDismiss={removeToast} />
          ))}
        </AnimatePresence>
      </div>
    </ToastContext.Provider>
  );
}
