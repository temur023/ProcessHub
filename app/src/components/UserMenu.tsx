import type React from 'react'
import { useContext } from 'react'
import { AccountMenu } from '@/components/AccountMenu'
import { PlatformShellContext } from '@/components/platform-shell-context'

/** Резервное меню для экрана без общей панели платформы. */
export const UserMenu: React.FC = () => {
  const inPlatformShell = useContext(PlatformShellContext)
  if (inPlatformShell) return null
  return <AccountMenu />
}
