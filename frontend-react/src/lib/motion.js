// Shared Framer Motion animation presets — import these everywhere instead of inline values

export const spring = {
  type: 'spring',
  stiffness: 400,
  damping: 30,
}

export const springGentle = {
  type: 'spring',
  stiffness: 250,
  damping: 28,
}

export const ease = {
  type: 'tween',
  ease: [0.22, 1, 0.36, 1],
  duration: 0.2,
}

export const easeFast = {
  type: 'tween',
  ease: [0.22, 1, 0.36, 1],
  duration: 0.15,
}

export const easeMedium = {
  type: 'tween',
  ease: [0.22, 1, 0.36, 1],
  duration: 0.3,
}

export const easeSlow = {
  type: 'tween',
  ease: [0.22, 1, 0.36, 1],
  duration: 0.5,
}

export const easeIn = {
  type: 'tween',
  ease: [0.4, 0, 1, 1],
  duration: 0.15,
}

// Card / item enter animation
export const fadeUp = {
  initial:  { opacity: 0, y: 12 },
  animate:  { opacity: 1, y: 0  },
  exit:     { opacity: 0, y: -8 },
  transition: ease,
}

// Slide in from right (activity feed items)
export const slideInRight = {
  initial:  { opacity: 0, x: 24 },
  animate:  { opacity: 1, x: 0  },
  exit:     { opacity: 0, x: 16 },
  transition: ease,
}

// Slide in from left
export const slideInLeft = {
  initial:  { opacity: 0, x: -24 },
  animate:  { opacity: 1, x: 0   },
  exit:     { opacity: 0, x: -16 },
  transition: ease,
}

// Modal scale up
export const scaleModal = {
  initial:  { opacity: 0, scale: 0.96 },
  animate:  { opacity: 1, scale: 1    },
  exit:     { opacity: 0, scale: 0.96 },
  transition: ease,
}

// Backdrop fade
export const fadeBackdrop = {
  initial:  { opacity: 0 },
  animate:  { opacity: 1 },
  exit:     { opacity: 0 },
  transition: easeFast,
}

// Slide up from bottom (footer dock)
export const slideUpDock = {
  initial:  { opacity: 0, y: 44 },
  animate:  { opacity: 1, y: 0  },
  exit:     { opacity: 0, y: 44 },
  transition: springGentle,
}

// Stagger container — apply to parent
export const staggerContainer = {
  animate: {
    transition: {
      staggerChildren: 0.025,
      delayChildren:   0.05,
    },
  },
}

// Stagger item — apply to each child (pairs with staggerContainer)
export const staggerItem = {
  initial:  { opacity: 0, y: 10 },
  animate:  { opacity: 1, y: 0  },
  transition: ease,
}

// Collapse/expand height (use with layout={true})
export const collapse = {
  initial:  { opacity: 0, height: 0 },
  animate:  { opacity: 1, height: 'auto' },
  exit:     { opacity: 0, height: 0 },
  transition: easeMedium,
}

// Confidence bar fill (violet spring)
export const barFill = (pct) => ({
  initial:  { scaleX: 0 },
  animate:  { scaleX: pct / 100 },
  transition: { ...spring, delay: 0.1 },
})

// Tab content switch
export const tabContent = (direction = 1) => ({
  initial:  { opacity: 0, x: direction * 16 },
  animate:  { opacity: 1, x: 0             },
  exit:     { opacity: 0, x: direction * -16 },
  transition: easeFast,
})
