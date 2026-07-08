import 'package:flutter/material.dart';

const statusLabels = {
  'discovered': 'candidate',
  'queued': 'queued',
  'downloaded': 'downloaded',
  'parsed': 'parsed',
  'embedded': 'ready',
  'failed': 'failed',
  'metadata_only': 'metadata only',
};

Color statusColor(BuildContext context, String status) => switch (status) {
      'embedded' => Colors.green,
      'failed' => Theme.of(context).colorScheme.error,
      'metadata_only' => Colors.orange,
      'discovered' => Theme.of(context).colorScheme.outline,
      _ => Theme.of(context).colorScheme.primary,
    };

Widget statusChip(BuildContext context, String status) {
  final c = statusColor(context, status);
  return Container(
    padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 3),
    decoration: BoxDecoration(color: c.withValues(alpha: 0.12), border: Border.all(color: c.withValues(alpha: 0.4))),
    child: Text(statusLabels[status] ?? status,
        style: TextStyle(color: c, fontSize: 12, fontWeight: FontWeight.w600)),
  );
}

String paperMeta(Map p) {
  final authors = (p['authors'] as List? ?? []).take(3).join(', ');
  return [
    if (authors.isNotEmpty) authors,
    if (p['year'] != null) '${p['year']}',
    '${p['citation_count'] ?? 0} citations',
    if (p['source'] == 's2') 'Semantic Scholar' else 'arXiv',
  ].join(' · ');
}

Future<bool> confirm(BuildContext context, String title, String message) async =>
    await showDialog<bool>(
      context: context,
      builder: (ctx) => AlertDialog(
        title: Text(title),
        content: Text(message),
        actions: [
          TextButton(onPressed: () => Navigator.pop(ctx, false), child: const Text('Cancel')),
          FilledButton(onPressed: () => Navigator.pop(ctx, true), child: const Text('Delete')),
        ],
      ),
    ) ??
    false;

void snack(BuildContext context, String msg) =>
    ScaffoldMessenger.of(context).showSnackBar(SnackBar(content: Text(msg)));
