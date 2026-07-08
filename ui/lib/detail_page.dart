import 'dart:async';
import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;
import 'package:pdfrx/pdfrx.dart';

import 'api.dart';

/// Dual-pane detail: PDF left, AI right (Extract / Sections / AI Summary / Deep Analysis).
class DetailPage extends StatefulWidget {
  const DetailPage({super.key, required this.paperId});
  final String paperId;
  @override
  State<DetailPage> createState() => _DetailPageState();
}

class _DetailPageState extends State<DetailPage> {
  final _pdf = PdfViewerController();
  Map<String, dynamic>? _paper;
  bool _panelOpen = true;

  @override
  void initState() {
    super.initState();
    Api.get('/papers/${widget.paperId}').then((r) {
      if (mounted) setState(() => _paper = r as Map<String, dynamic>);
    }).catchError((_) {});
  }

  void _goToPage(int? page) {
    if (page != null && _pdf.isReady) _pdf.goToPage(pageNumber: page);
  }

  @override
  Widget build(BuildContext context) => Scaffold(
        appBar: AppBar(title: Text(_paper?['title'] ?? '…', overflow: TextOverflow.ellipsis)),
        body: Row(children: [
          Expanded(
            child: _paper == null
                ? const SizedBox()
                : _paper!['has_pdf'] == true
                    ? PdfViewer.uri(Uri.parse('$apiBase/papers/${widget.paperId}/pdf'),
                        controller: _pdf)
                    : const Center(child: Text('No PDF available for this paper (metadata only).')),
          ),
          const VerticalDivider(width: 1),
          if (!_panelOpen)
            SizedBox(
              width: 48,
              child: Column(children: [
                IconButton(
                  tooltip: 'Expand panel',
                  icon: const Icon(Icons.chevron_left),
                  onPressed: () => setState(() => _panelOpen = true),
                ),
              ]),
            )
          else
            Expanded(
              child: _paper == null
                  ? const Center(child: CircularProgressIndicator())
                  : DefaultTabController(
                      length: 4,
                      child: Column(children: [
                        Row(children: [
                          const Expanded(
                            child: TabBar(tabs: [
                              Tab(text: 'Extract'),
                              Tab(text: 'Sections'),
                              Tab(text: 'AI Summary'),
                              Tab(text: 'Deep Analysis'),
                            ]),
                          ),
                          IconButton(
                            tooltip: 'Collapse panel',
                            icon: const Icon(Icons.chevron_right),
                            onPressed: () => setState(() => _panelOpen = false),
                          ),
                        ]),
                        Expanded(
                          child: TabBarView(children: [
                            ExtractTab(paperId: widget.paperId, onPageTap: _goToPage),
                            _sections(),
                            SummaryTab(paperId: widget.paperId, onCitationTap: _goToPage),
                            AnalyzeTab(paperId: widget.paperId),
                          ]),
                        ),
                      ]),
                    ),
            ),
        ]),
      );

  Widget _sections() {
    final sections = (_paper!['sections'] as Map<String, dynamic>);
    if (sections.isEmpty) return const Center(child: Text('No section data.'));
    return ListView(
      children: [
        for (final e in sections.entries) ...[
          ListTile(
              title: Text(e.key.toUpperCase(),
                  style: const TextStyle(fontWeight: FontWeight.bold))),
          for (final p in e.value as List)
            ListTile(
              dense: true,
              leading: Text('p.${p['page'] ?? '?'}'),
              title: Text(p['preview'] ?? '', maxLines: 2, overflow: TextOverflow.ellipsis),
              onTap: () => _goToPage(p['page']),
            ),
        ]
      ],
    );
  }
}

// ---------------- Extract: abstract + conclusion, no LLM ----------------

class ExtractTab extends StatefulWidget {
  const ExtractTab({super.key, required this.paperId, required this.onPageTap});
  final String paperId;
  final void Function(int?) onPageTap;
  @override
  State<ExtractTab> createState() => _ExtractTabState();
}

class _ExtractTabState extends State<ExtractTab> with AutomaticKeepAliveClientMixin {
  Map<String, dynamic>? _data;
  String? _error;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    Api.get('/papers/${widget.paperId}/extract').then((r) {
      if (mounted) setState(() => _data = r as Map<String, dynamic>);
    }).catchError((e) {
      if (mounted) setState(() => _error = '$e');
    });
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    if (_error != null) return Center(child: Text(_error!));
    if (_data == null) return const Center(child: CircularProgressIndicator());
    final conclusion = (_data!['conclusion'] ?? []) as List;
    final style = Theme.of(context).textTheme;
    return ListView(padding: const EdgeInsets.all(16), children: [
      Text('ABSTRACT', style: style.titleSmall),
      const SizedBox(height: 6),
      SelectableText(_data!['abstract'] ?? 'Abstract not found.'),
      const Divider(height: 32),
      Text('CONCLUSION & FUTURE WORK', style: style.titleSmall),
      const SizedBox(height: 6),
      if (conclusion.isEmpty)
        const Text('This section was not found in the paper.',
            style: TextStyle(fontStyle: FontStyle.italic))
      else
        for (final c in conclusion) ...[
          SelectableText(c['text'] ?? ''),
          if (c['page'] != null)
            Align(
              alignment: Alignment.centerRight,
              child: TextButton(
                  onPressed: () => widget.onPageTap(c['page']), child: Text('p.${c['page']} →')),
            ),
          const SizedBox(height: 12),
        ],
    ]);
  }
}

// ---------------- Summary matrix tab (LLM, grounded citations) ----------------

class SummaryTab extends StatefulWidget {
  const SummaryTab({super.key, required this.paperId, required this.onCitationTap});
  final String paperId;
  final void Function(int?) onCitationTap;
  @override
  State<SummaryTab> createState() => _SummaryTabState();
}

class _SummaryTabState extends State<SummaryTab> with AutomaticKeepAliveClientMixin {
  Map<String, dynamic>? _matrix;
  String? _error;

  @override
  bool get wantKeepAlive => true;

  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load({bool refresh = false}) async {
    setState(() {
      _matrix = null;
      _error = null;
    });
    try {
      final r = await Api.get('/papers/${widget.paperId}/summary',
          refresh ? {'refresh': 'true'} : null);
      if (mounted) setState(() => _matrix = r as Map<String, dynamic>);
    } catch (e) {
      if (mounted) setState(() => _error = '$e');
    }
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    if (_error != null) return Center(child: Text(_error!));
    if (_matrix == null) {
      return const Center(
          child: Column(mainAxisSize: MainAxisSize.min, children: [
        CircularProgressIndicator(),
        SizedBox(height: 12),
        Text('Generating grounded summary…'),
      ]));
    }
    final sections = _matrix!['sections'] as Map<String, dynamic>;
    return ListView(padding: const EdgeInsets.all(12), children: [
      Align(
        alignment: Alignment.centerRight,
        child: TextButton.icon(
            onPressed: () => _load(refresh: true),
            icon: const Icon(Icons.refresh, size: 16),
            label: const Text('Regenerate')),
      ),
      for (final e in sections.entries) ...[
        Text(e.key.toUpperCase(), style: Theme.of(context).textTheme.titleMedium),
        const SizedBox(height: 6),
        if (e.value['summary'] != null) ...[
          SelectableText(e.value['summary']),
          Wrap(spacing: 6, children: [
            for (final c in (e.value['citations'] ?? []) as List)
              ActionChip(
                label: Text('p.${c['page']} ¶${c['paragraph']}'),
                onPressed: () => widget.onCitationTap(c['page']),
              ),
            for (final u in (e.value['unverified'] ?? []) as List)
              Chip(
                  label: Text('⚠ unverified $u'),
                  backgroundColor: Colors.orange.withValues(alpha: 0.2)),
          ]),
        ] else
          Text(e.value['note'] ?? 'n/a', style: const TextStyle(fontStyle: FontStyle.italic)),
        const Divider(height: 24),
      ]
    ]);
  }
}

// ---------------- Deep analysis (SSE stream) tab ----------------

class AnalyzeTab extends StatefulWidget {
  const AnalyzeTab({super.key, required this.paperId});
  final String paperId;
  @override
  State<AnalyzeTab> createState() => _AnalyzeTabState();
}

class _AnalyzeTabState extends State<AnalyzeTab> with AutomaticKeepAliveClientMixin {
  final _topic = TextEditingController();
  final _scroll = ScrollController();
  String _output = '';
  bool _running = false;

  @override
  bool get wantKeepAlive => true;

  Future<void> _run() async {
    setState(() {
      _running = true;
      _output = '';
    });
    final r = await Api.post('/analyze', {'paper_id': widget.paperId, 'topic': _topic.text});
    final req = http.Request('GET', Uri.parse('$apiBase${r['stream_url']}'));
    final resp = await http.Client().send(req);
    resp.stream.transform(utf8.decoder).transform(const LineSplitter()).listen((line) {
      if (!line.startsWith('data: ')) return;
      final payload = line.substring(6);
      if (payload == '[DONE]' || payload.startsWith('[ERROR]')) {
        setState(() => _running = false);
        return;
      }
      setState(() => _output += jsonDecode(payload) as String);
      if (_scroll.hasClients) _scroll.jumpTo(_scroll.position.maxScrollExtent);
    }, onDone: () => setState(() => _running = false));
  }

  @override
  Widget build(BuildContext context) {
    super.build(context);
    return Column(children: [
      Padding(
        padding: const EdgeInsets.all(12),
        child: Row(children: [
          Expanded(
            child: TextField(
              controller: _topic,
              decoration: const InputDecoration(
                  hintText: 'Topic to analyze in depth (a method or gap from the summary)'),
            ),
          ),
          const SizedBox(width: 8),
          FilledButton(
              onPressed: _running ? null : _run,
              child: _running
                  ? const SizedBox(
                      width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Text('Analyze')),
        ]),
      ),
      Expanded(
        child: SingleChildScrollView(
          controller: _scroll,
          padding: const EdgeInsets.all(12),
          child: SelectableText(_output.isEmpty ? 'The blueprint will stream here.' : _output),
        ),
      ),
    ]);
  }
}
