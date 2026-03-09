"""Decision Tracer - Trace les décisions de détection de dates.

Ce module permet de tracer chaque étape de la détection de dates
pour comprendre pourquoi un DocId a reçu telle date.

Utile pour debugging les cas problématiques où :
- PDatum ne correspond pas à l'UI
- Le hint n'est pas appliqué correctement
- La stratégie last_in_row échoue

Usage:
    # Activer via env var
    export DEBUG_DATES=1
    scrapy crawl fribourg -a days=30
    
    # Les traces sont sauvegardées dans output/traces/{docid}_trace.json
"""

import json
import logging
from pathlib import Path
from datetime import datetime
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class DecisionTracer:
    """Trace les décisions de détection de dates pour debug.
    
    Génère un fichier JSON par DocId contenant toutes les étapes :
    - EDatum detection (où trouvé, quel offset, etc.)
    - PDatum strategies (last_in_row, content_scan, etc.)
    - Hint application (upgrade/clamp/rescue)
    - Validation finale
    
    Attributes:
        enabled: Activer/désactiver le tracing
        output_dir: Répertoire de sortie des traces
        traces: Buffer des traces en mémoire
    
    Example:
        >>> tracer = DecisionTracer(enabled=True, output_dir=Path('output/traces'))
        >>> 
        >>> tracer.trace_date_detection(
        ...     doc_id='abc123...',
        ...     stage='edatum_detection',
        ...     decision='Found at position +3',
        ...     context={'offset': 3, 'token': '2026-01-30'}
        ... )
        >>> 
        >>> tracer.save('abc123...')
        # Fichier créé: output/traces/abc123..._trace.json
    """
    
    def __init__(self, enabled: bool = False, output_dir: Optional[Path] = None):
        """Initialize tracer.
        
        Args:
            enabled: Activer le tracing (coûteux, seulement pour debug)
            output_dir: Répertoire de sortie (default: output/traces)
        """
        self.enabled = enabled
        self.output_dir = output_dir or Path('output/traces')
        self.traces: List[Dict[str, Any]] = []
        
        if self.enabled:
            try:
                self.output_dir.mkdir(parents=True, exist_ok=True)
                logger.info(f"Decision tracing enabled: {self.output_dir}")
            except Exception as e:
                logger.error(f"Cannot create trace dir {self.output_dir}: {e}")
                self.enabled = False
    
    def trace_date_detection(
        self,
        doc_id: str,
        stage: str,
        decision: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Enregistre une étape de détection de dates.
        
        Args:
            doc_id: DocId concerné
            stage: Étape du process (ex: 'edatum_detection', 'hint_application')
            decision: Décision prise (ex: 'Found at +3', 'Upgrade applied')
            context: Données de contexte (offset, tokens, dates, etc.)
        
        Example:
            >>> tracer.trace_date_detection(
            ...     doc_id='abc123',
            ...     stage='edatum_detection',
            ...     decision='Found at position +3',
            ...     context={
            ...         'offset': 3,
            ...         'token': '2026-01-30',
            ...         'parsed': '2026-01-30',
            ...     }
            ... )
        """
        if not self.enabled:
            return
        
        trace = {
            'timestamp': datetime.now().isoformat(),
            'doc_id': doc_id,
            'stage': stage,
            'decision': decision,
            'context': context or {},
        }
        
        self.traces.append(trace)
    
    def trace_hint_calculation(
        self,
        doc_id: str,
        chunk_dates: List[str],
        marker_candidate: Optional[str],
        marker_accepted: bool,
        reason: str,
    ) -> None:
        """Trace le calcul du pdatum_hint (marker propagation).
        
        Args:
            doc_id: DocId concerné
            chunk_dates: Dates trouvées dans le chunk
            marker_candidate: Date candidate comme marker
            marker_accepted: Si le marker a été accepté
            reason: Raison de la décision
        
        Example:
            >>> tracer.trace_hint_calculation(
            ...     doc_id='abc123',
            ...     chunk_dates=['2026-01-30', '2026-01-15', '2025-12-10'],
            ...     marker_candidate='2026-01-30',
            ...     marker_accepted=True,
            ...     reason='2+ distinct dates, max=2026-01-30'
            ... )
        """
        if not self.enabled:
            return
        
        self.trace_date_detection(
            doc_id=doc_id,
            stage='hint_calculation',
            decision='Marker created' if marker_accepted else 'No marker',
            context={
                'chunk_dates': chunk_dates,
                'distinct_count': len(set(chunk_dates)),
                'marker_candidate': marker_candidate,
                'marker_accepted': marker_accepted,
                'reason': reason,
            }
        )
    
    def trace_hint_application(
        self,
        doc_id: str,
        pdatum_local: Optional[str],
        pdatum_hint: Optional[str],
        pdatum_final: Optional[str],
        action: str,
        reason: str,
    ) -> None:
        """Trace l'application du pdatum_hint.
        
        Args:
            doc_id: DocId concerné
            pdatum_local: PDatum détecté localement (tokens/content)
            pdatum_hint: PDatum hint (marker propagé)
            pdatum_final: PDatum final après application du hint
            action: Action prise ('upgrade', 'clamp', 'rescue', 'keep', 'skip')
            reason: Raison de la décision
        
        Example:
            >>> tracer.trace_hint_application(
            ...     doc_id='abc123',
            ...     pdatum_local='2026-01-15',
            ...     pdatum_hint='2026-01-30',
            ...     pdatum_final='2026-01-30',
            ...     action='upgrade',
            ...     reason='hint > local'
            ... )
        """
        if not self.enabled:
            return
        
        self.trace_date_detection(
            doc_id=doc_id,
            stage='hint_application',
            decision=action,
            context={
                'pdatum_local': pdatum_local,
                'pdatum_hint': pdatum_hint,
                'pdatum_final': pdatum_final,
                'reason': reason,
            }
        )
    
    def trace_content_scan(
        self,
        doc_id: str,
        candidates: List[Dict[str, Any]],
        best_candidate: Optional[str],
        score: Optional[tuple],
    ) -> None:
        """Trace le scan du contenu brut pour PDatum.
        
        Args:
            doc_id: DocId concerné
            candidates: Liste des candidats avec positions et scores
            best_candidate: Meilleur candidat retenu
            score: Score du meilleur candidat (is_before, -date, distance)
        
        Example:
            >>> tracer.trace_content_scan(
            ...     doc_id='abc123',
            ...     candidates=[
            ...         {'date': '2026-01-30', 'pos': 100, 'score': (0, -20260130, 50)},
            ...         {'date': '2026-01-15', 'pos': 50, 'score': (1, -20260115, 20)},
            ...     ],
            ...     best_candidate='2026-01-30',
            ...     score=(0, -20260130, 50)
            ... )
        """
        if not self.enabled:
            return
        
        self.trace_date_detection(
            doc_id=doc_id,
            stage='content_scan',
            decision='Best candidate selected' if best_candidate else 'No candidate',
            context={
                'candidates_count': len(candidates),
                'candidates': candidates[:10],  # Limiter à 10 pour lisibilité
                'best_candidate': best_candidate,
                'best_score': score,
            }
        )
    
    def save(self, doc_id: str) -> bool:
        """Sauvegarde les traces pour un DocId.
        
        Args:
            doc_id: DocId à sauvegarder
            
        Returns:
            True si sauvegarde réussie, False sinon
        """
        if not self.enabled:
            return False
        
        # Filtrer les traces pour ce DocId
        doc_traces = [t for t in self.traces if t['doc_id'] == doc_id]
        
        if not doc_traces:
            return False
        
        # Générer le nom de fichier
        safe_docid = doc_id[:16]  # Limiter à 16 chars
        filename = f"{safe_docid}_trace.json"
        filepath = self.output_dir / filename
        
        try:
            filepath.write_text(json.dumps(doc_traces, indent=2), encoding='utf-8')
            logger.debug(f"Trace saved: {filepath}")
            return True
        except Exception as e:
            logger.error(f"Trace save error for {doc_id}: {e}")
            return False
    
    def clear(self) -> None:
        """Efface toutes les traces en mémoire."""
        self.traces.clear()
    
    def get_summary(self) -> Dict[str, Any]:
        """Génère un résumé des traces.
        
        Returns:
            Dict avec statistiques (count par stage, etc.)
        """
        from collections import Counter
        
        stages = Counter(t['stage'] for t in self.traces)
        docids = set(t['doc_id'] for t in self.traces)
        
        return {
            'total_traces': len(self.traces),
            'docids_traced': len(docids),
            'stages': dict(stages),
        }
