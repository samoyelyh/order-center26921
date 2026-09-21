# Phase 1 迁移：设计包 → 上传 → Asset → 主素材 → 位置
#
# 建表顺序（解决 materials.current_psd_revision_id 的循环 FK）：
#   1. design_packages
#   2. package_uploads
#   3. assets
#   4. materials                （current_psd_revision_id 先不建 FK）
#   5. material_psd_revisions
#   6. ALTER materials 补 current_psd_revision_id FK
#   7. design_package_materials
#   8. upload_sessions          （FK package_uploads）
#   9. code_sequences
#  10. activity_logs
"""
