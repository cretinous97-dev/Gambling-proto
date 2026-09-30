import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import { api } from '../lib/api.js'
import { Card, Loading, Alert } from '../components/ui.jsx'

export default function Legal() {
  const { doc } = useParams()
  const [data, setData] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    setData(null)
    api.legal(doc).then(setData).catch((e) => setError(e.message))
  }, [doc])

  if (error) return <div className="page narrow"><Alert kind="error">{error}</Alert></div>
  if (!data) return <div className="page narrow"><Loading /></div>

  return (
    <div className="page medium">
      <Card title={data.title}>
        <Alert kind="warn">
          These documents are TEMPLATES. Every section marked "OPERATOR MUST INSERT" is a legal
          obligation that has not been filled in — completing it, and getting the text reviewed by a
          gaming lawyer for each market you serve, is part of going live.
        </Alert>
        {data.sections.map((s) => (
          <div key={s.heading} style={{ marginBottom: 16 }}>
            <h4 style={{ marginBottom: 4 }}>{s.heading}</h4>
            <p className="small muted" style={{ marginTop: 0 }}>{s.body}</p>
          </div>
        ))}
      </Card>
    </div>
  )
}
