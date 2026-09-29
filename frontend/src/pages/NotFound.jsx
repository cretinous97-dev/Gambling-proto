import { Link } from 'react-router-dom'
import { Card } from '../components/ui.jsx'

export default function NotFound() {
  return (
    <div className="page narrow">
      <Card title="Page not found">
        <p className="muted">That page does not exist.</p>
        <Link className="btn btn-primary" to="/">Back to the lobby</Link>
      </Card>
    </div>
  )
}
