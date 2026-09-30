import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { Card } from '../components/ui.jsx'

export default function NotFound() {
  const { t } = useTranslation()
  return (
    <div className="page narrow">
      <Card title="Page not found">
        <p className="muted">{t('common.not_found')}</p>
        <Link className="btn btn-primary" to="/">Back to the lobby</Link>
      </Card>
    </div>
  )
}
