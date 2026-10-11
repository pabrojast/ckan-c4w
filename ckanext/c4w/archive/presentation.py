"""Read-only catalogue presentation from the active, audited archive manifest."""
from html import escape


COPY = {
    'en': {
        'version': 'Version', 'observations': 'Original observations',
        'download': 'Download the complete original archive (ZIP)',
        'legacy': 'Earlier snapshot downloads',
        'description': 'Complete Global Freshwater Quality Archive, version {version}. Explore the original observations by parameter, station, country and period. Counts and coverage below come from the active archive files.',
        'provenance': 'The original source files are preserved with checksums, source filenames and row identities. Repeated observations are retained; identical station metadata rows are collapsed only for the station catalogue.',
        'methodology': 'Maps, annual medians and distributions are calculated from eligible original measurements on the server. Statistics keep compatible units and sample integration types separate; they do not average station medians.',
        'quality': 'Statistics use uncensored, finite values with Good, Fair, Unknown or unspecified quality. Limits (<, >), estimated and suspect values remain in the original table. Colours describe the data, not regulatory quality.',
    },
    'es': {
        'version': 'Versión', 'observations': 'Observaciones originales',
        'download': 'Descargar el archivo original completo (ZIP)',
        'legacy': 'Descargas de la versión anterior',
        'description': 'Archivo mundial completo de calidad del agua dulce, versión {version}. Explora las observaciones originales por parámetro, estación, país y período. Los conteos y la cobertura proceden de los archivos activos.',
        'provenance': 'Los archivos originales se conservan con sumas de verificación, nombres de archivo e identificadores de fila. Se mantienen las observaciones repetidas; solo se unifican las filas idénticas de metadatos de estaciones para el catálogo.',
        'methodology': 'Los mapas, las medianas anuales y las distribuciones se calculan en el servidor a partir de las mediciones originales elegibles. Las estadísticas separan unidades compatibles y tipos de integración de muestra; no promedian medianas de estaciones.',
        'quality': 'Las estadísticas usan valores finitos sin censura con calidad Good, Fair, Unknown o no especificada. Los límites (<, >), valores estimados y sospechosos permanecen en la tabla original. Los colores describen los datos, no su calidad normativa.',
    },
    'fr': {
        'version': 'Version', 'observations': 'Observations originales',
        'download': 'Télécharger les archives originales complètes (ZIP)',
        'legacy': 'Téléchargements de la version précédente',
        'description': 'Archives mondiales complètes de la qualité de l’eau douce, version {version}. Explorez les observations originales par paramètre, station, pays et période. Les effectifs et la couverture proviennent des fichiers actifs.',
        'provenance': 'Les fichiers originaux sont conservés avec leurs sommes de contrôle, noms de fichier et identifiants de ligne. Les observations répétées sont conservées ; seules les lignes de métadonnées de station identiques sont regroupées pour le catalogue.',
        'methodology': 'Les cartes, médianes annuelles et distributions sont calculées sur le serveur à partir des mesures originales admissibles. Les statistiques séparent les unités compatibles et les types d’intégration des échantillons ; elles ne moyennent pas les médianes des stations.',
        'quality': 'Les statistiques utilisent les valeurs finies non censurées de qualité Good, Fair, Unknown ou non précisée. Les limites (<, >), estimations et valeurs suspectes restent dans le tableau original. Les couleurs décrivent les données, pas une classification réglementaire.',
    },
    'ar': {
        'version': 'الإصدار', 'observations': 'القياسات الأصلية',
        'download': 'تنزيل الأرشيف الأصلي الكامل (ZIP)',
        'legacy': 'تنزيلات النسخة السابقة',
        'description': 'الأرشيف العالمي الكامل لجودة المياه العذبة، الإصدار {version}. استكشف القياسات الأصلية حسب المعلمة والمحطة والبلد والفترة. تستند الأعداد والتغطية إلى ملفات الأرشيف النشطة.',
        'provenance': 'تُحفظ الملفات الأصلية مع بصمات التحقق وأسماء الملفات ومعرّفات الصفوف. تُحفظ القياسات المتكررة، وتُدمج فقط صفوف بيانات المحطات المتطابقة عند إعداد فهرس المحطات.',
        'methodology': 'تُحسب الخرائط والقيم الوسيطة السنوية والتوزيعات على الخادم من القياسات الأصلية المؤهلة. تُفصل الوحدات المتوافقة وأنواع تكامل العينات في الإحصاءات، ولا يُحسب متوسط القيم الوسيطة للمحطات.',
        'quality': 'تستخدم الإحصاءات القيم المحدودة عددياً وغير المقيدة بحدود كشف، ذات جودة Good أو Fair أو Unknown أو غير محددة. تبقى حدود القياس (<، >) والقيم التقديرية والمشكوك فيها في الجدول الأصلي. تصف الألوان البيانات ولا تمثل تصنيفاً تنظيمياً للجودة.',
    },
}


def detail_dataset(dataset, manifest, language='en'):
    """Overlay display fields without altering the database or legacy bundle."""
    copy = COPY.get(str(language).replace('_', '-').split('-')[0], COPY['en'])
    starts = [p['min_year'] for p in manifest['parameters'] if p.get('min_year') is not None]
    ends = [p['max_year'] for p in manifest['parameters'] if p.get('max_year') is not None]
    result = dict(dataset)
    result.update({
        'record_count': manifest['observations'],
        'site_count': manifest['dimensions']['station_ids'],
        'parameter_count': manifest['observed_parameter_codes'],
        'temporal_start': str(min(starts)) if starts else '',
        'temporal_end': str(max(ends)) if ends else '',
        'grain': '', 'license_id': manifest['license'],
        'doi': manifest['doi'], 'source_url': 'https://doi.org/' + manifest['doi'],
        'citation': 'UNEP GEMS/Water. Global Freshwater Quality Archive (%s). DOI: %s. %s.' %
                    (manifest['version'], manifest['doi'], manifest['license']),
        'description': copy['description'].format(version=escape(manifest['version'])),
        'provenance': escape(copy['provenance']),
        'methodology': escape(copy['methodology']),
        'data_quality_note': escape(copy['quality']),
    })
    archive = {k: manifest[k] for k in ('version', 'license', 'source_url', 'zip_bytes')}
    archive.update(labels=copy, countries=manifest['dimensions']['countries'])
    return result, archive
