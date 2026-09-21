# ============================================================================
# SQLAlchemy 2.x ORM 模型
#
# Phase 1（文件入库）：
#   design_packages / package_uploads / assets / materials /
#   material_psd_revisions / design_package_materials / upload_sessions /
#   package_upload_assets（上传行为 ↔ Asset 多对多）/
#   code_sequences（MAT 编码并发安全发放器）/ activity_logs（统一维护记录）
#
# Phase 2（副素材按同名 pairKey 配对 + 建版）：
#   derivative_batches / material_variants / variant_revisions / material_pairings
#
# 订单识别 Phase A-D：
#   order_import_batches / order_items / order_import_batch_items /
#   order_buyer_assets
# ============================================================================

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.mysql import BIGINT, BINARY, DATETIME, JSON, VARCHAR
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def _ts() -> Mapped[datetime]:
    return mapped_column(DATETIME(fsp=6), nullable=False, server_default=func.now(6))


# ---------------------------------------------------------------- 设计包


class DesignPackage(Base):
    """设计包：长期逻辑实体。不存任何缓存业务字段（计数/版本号/运营由查询聚合）。"""

    __tablename__ = "design_packages"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    code: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    name: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)

    # 设计编码：**用户/美工自己的设计编号**，上传时手填。
    # 一个设计编码代表一个「设计」，可以跨多个设计包（同一设计的新一版仍用同一个编码）；
    # code 是系统生成的设计包编码（DP-...），两者不是一回事。
    # 素材的「关联设计」= 引用它的设计包的 design_code 去重后的集合，因此这里不建唯一约束。
    design_code: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)

    # 订单候选隔离用稳定品类码；UNKNOWN 表示尚未归类，不参与跨品类猜测。
    category_code: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, default="UNKNOWN")
    category_name: Mapped[str] = mapped_column(VARCHAR(128), nullable=False, default="未知品类")

    # 标签：上传设计包时填写；创建时向下继承（主素材/副素材各复制一份），之后独立修改
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # 负责人：业务上负责这套设计的人（可只有姓名，没有 userId）。
    # 注意与「实际上传人」区分开：uploader 在 package_uploads 上，是点了上传的那个人。
    responsible_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    responsible_name: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)

    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 设计美工：设计包的**长期业务属性**（这个包是谁设计的），不是「谁点了上传」。
    # designer_id 只有接入用户中心后才有值；当前允许只有姓名。
    designer_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    designer_name: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)

    created_by: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6), onupdate=func.now(6)
    )
    archived_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)

    uploads: Mapped[list["PackageUpload"]] = relationship(back_populates="design_package")

    __table_args__ = (
        UniqueConstraint("code", name="uq_design_packages_code"),
        Index("ix_design_packages_design_code", "design_code"),
        Index("ix_design_packages_created_at", "created_at"),
        Index("ix_design_packages_archived_at", "archived_at"),
        {"comment": "设计包（长期逻辑实体）"},
    )


# ---------------------------------------------------------------- 上传行为


class PackageUpload(Base):
    """每一次真实的文件包上传行为。originalPackageName / uploader / uploadSessionId 都在这里。"""

    __tablename__ = "package_uploads"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)

    design_package_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("design_packages.id", ondelete="CASCADE"), nullable=False
    )

    original_package_name: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    file_size: Mapped[int | None] = mapped_column(BIGINT, nullable=True)

    upload_session_id: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)

    # 实际上传人：**这一次**是谁把文件传上来的（与设计包的「设计美工」是两件事）。
    # 当前没有登录系统，页面允许手工填写，因此 uploader_id 允许为空；
    # 接入登录后由登录用户自动回填并改为只读。
    uploader_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    uploader_name: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)

    upload_type: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    target_batch_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)

    status: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="PARSING")

    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6), onupdate=func.now(6)
    )

    design_package: Mapped[DesignPackage] = relationship(back_populates="uploads")

    __table_args__ = (
        UniqueConstraint("upload_session_id", name="uq_package_uploads_session"),
        CheckConstraint(
            "upload_type IN ('INITIAL','NEW_BATCH','SUPPLEMENT','REVISION')",
            name="ck_package_uploads_type",
        ),
        CheckConstraint(
            "status IN ('PARSING','PARSED','MATCHED','CONFIRMED','SUBMITTED','INTERRUPTED','FAILED')",
            name="ck_package_uploads_status",
        ),
        Index("ix_package_uploads_pkg_created", "design_package_id", "created_at"),
        {"comment": "每次实际的文件包上传行为"},
    )


# ---------------------------------------------------------------- Asset


class Asset(Base):
    """
    所有真实文件的唯一文件实体。业务表只通过 asset_id 引用，不复制 URL / hash。

    注意：Asset **不承担上传归属职责**。
    一个物理文件（同一 BLAKE3）可以被多次上传复用，归属关系记录在
    package_upload_assets（多对多）里，见 PackageUploadAsset。
    """

    __tablename__ = "assets"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)

    storage_key: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    original_filename: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)

    mime_type: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)

    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    blake3: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    # 64bit pHash：MySQL BINARY(8)；ORM 层用 bytes
    phash: Mapped[bytes | None] = mapped_column(BINARY(8), nullable=True)
    phash_version: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_by: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    created_at: Mapped[datetime] = _ts()
    deleted_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)

    __table_args__ = (
        UniqueConstraint("storage_key", name="uq_assets_storage_key"),
        # BLAKE3 是所有文件的精确去重指纹；pHash 仅对可解码图片可选。
        CheckConstraint(
            "phash_version IS NULL OR phash IS NOT NULL",
            name="ck_assets_phash_version",
        ),
        # BLAKE3 用于「完全相同文件」查询，但不做 UNIQUE（重复时由业务层复用已有 Asset）
        Index("ix_assets_blake3", "blake3"),
        Index("ix_assets_phash", "phash", "phash_version"),
        Index("ix_assets_deleted_at", "deleted_at"),
        {"comment": "文件资产（唯一文件实体，不承担上传归属）"},
    )


class PackageUploadAsset(Base):
    """
    上传行为 ↔ Asset 的关联（多对多）。

    - 一次上传可以包含多个文件（主图 / PSD / 副图 / 其他）
    - 同一个 Asset（BLAKE3 完全一致的物理文件）可以被多次上传复用
      → 因此归属关系必须放在这张表，而不是 assets 上的单个字段
    - 旧上传关系永不被新上传破坏（只新增关联行）

    file_role 只表示「这是主图/PSD/副图/其他」。

    pair_key = **同一次上传内的同名配对键**（文件名去掉扩展名，如
    main/1.jpg、psd/1.psd、variant/1.jpg 都是 "1"）。
    它只在当前设计包内用于把主图/PSD/副图配到一起，**不是永久身份**；
    永久主素材身份仍然是 MAT-xxxxxx。
    """

    __tablename__ = "package_upload_assets"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)

    package_upload_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("package_uploads.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )

    file_role: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)
    original_filename: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    # 同名配对键（小写，不含扩展名）。解析不出时为空字符串 → 由配对逻辑报「无法解析 pairKey」
    pair_key: Mapped[str] = mapped_column(VARCHAR(255), nullable=False, default="")
    # 从文件名提取的位置提示（1.jpg → 1、3-1.jpg → 3），仅用于展示排序
    position_hint: Mapped[int | None] = mapped_column(Integer, nullable=True)

    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        # 同一次上传里，同一角色下不允许出现重复的 pair_key（重复就是异常，必须让用户看到）
        UniqueConstraint(
            "package_upload_id", "file_role", "pair_key", name="uq_pua_upload_role_pairkey"
        ),
        CheckConstraint(
            "file_role IN ('MAIN_PREVIEW','PSD','VARIANT','OTHER')",
            name="ck_pua_file_role",
        ),
        CheckConstraint(
            "position_hint IS NULL OR (position_hint >= 1 AND position_hint <= 9999)",
            name="ck_pua_position_hint",
        ),
        Index("ix_pua_upload_role", "package_upload_id", "file_role"),
        Index("ix_pua_upload_pairkey", "package_upload_id", "pair_key"),
        Index("ix_pua_asset", "asset_id"),
        {"comment": "上传行为 ↔ Asset 多对多关联（含文件角色 + 同名配对键）"},
    )


# ---------------------------------------------------------------- Material


class Material(Base):
    """主素材永久实体 MAT-xxxxxx。只代表主素材；副素材将来独立在 material_variants。"""

    __tablename__ = "materials"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    material_code: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    name: Mapped[str] = mapped_column(VARCHAR(255), nullable=False)

    preview_asset_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    # 循环 FK：由 Alembic 在建表后 ALTER TABLE 补上
    current_psd_revision_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)

    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)

    source_package_upload_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("package_uploads.id", ondelete="SET NULL"), nullable=True
    )

    created_by: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6), onupdate=func.now(6)
    )
    archived_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)

    psd_revisions: Mapped[list["MaterialPsdRevision"]] = relationship(
        back_populates="material",
        foreign_keys="MaterialPsdRevision.material_id",
    )

    __table_args__ = (
        UniqueConstraint("material_code", name="uq_materials_code"),
        Index("ix_materials_created_at", "created_at"),
        Index("ix_materials_preview_asset", "preview_asset_id"),
        {"comment": "主素材（MAT-xxxxxx 永久实体）"},
    )


class MaterialPsdRevision(Base):
    """主素材 PSD 修订历史。旧 Revision 永不覆盖删除（deleted 软删）。"""

    __tablename__ = "material_psd_revisions"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    material_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("materials.id", ondelete="CASCADE"), nullable=False
    )
    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )

    created_by: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    created_at: Mapped[datetime] = _ts()

    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    material: Mapped[Material] = relationship(
        back_populates="psd_revisions", foreign_keys=[material_id]
    )

    __table_args__ = (
        UniqueConstraint("material_id", "revision_no", name="uq_psd_revisions_no"),
        CheckConstraint("revision_no >= 1", name="ck_psd_revisions_no_positive"),
        Index("ix_psd_revisions_asset", "asset_id"),
        {"comment": "主素材 PSD 修订历史"},
    )


# ---------------------------------------------------------------- 设计包位置


class DesignPackageMaterial(Base):
    """设计包位置 → 永久 MAT 的关联。文件信息一律通过 source_asset_id 获取。"""

    __tablename__ = "design_package_materials"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)

    design_package_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("design_packages.id", ondelete="CASCADE"), nullable=False
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    material_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False
    )

    display_name: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    source_file_name: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)

    # 该位置本次上传所用的真实文件
    source_asset_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )

    created_from_upload_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("package_uploads.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("design_package_id", "position", name="uq_dpm_pkg_position"),
        CheckConstraint("position >= 1", name="ck_dpm_position_positive"),
        Index("ix_dpm_material", "material_id"),
        Index("ix_dpm_pkg_position", "design_package_id", "position"),
        {"comment": "设计包位置 → MAT 关联"},
    )


# ---------------------------------------------------------------- 上传会话


class UploadSession(Base):
    """上传会话：幂等 / 刷新恢复 / 断点续传锚点。"""

    __tablename__ = "upload_sessions"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)

    design_package_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("design_packages.id", ondelete="CASCADE"), nullable=False
    )
    package_upload_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("package_uploads.id", ondelete="CASCADE"), nullable=True
    )

    original_package_name: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    file_size: Mapped[int] = mapped_column(BIGINT, nullable=False, default=0)

    stage: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="PARSING")
    upload_type: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="INITIAL")

    operator_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    operator_name: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)

    remark: Mapped[str | None] = mapped_column(Text, nullable=True)

    received_files: Mapped[list | None] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6), onupdate=func.now(6)
    )

    __table_args__ = (
        UniqueConstraint("package_upload_id", name="uq_upload_sessions_upload"),
        CheckConstraint(
            "stage IN ('PARSING','PARSED','MATCHED','CONFIRMED','SUBMITTED','INTERRUPTED','FAILED')",
            name="ck_upload_sessions_stage",
        ),
        Index("ix_upload_sessions_pkg_stage", "design_package_id", "stage"),
        {"comment": "上传会话（幂等锚点）"},
    )


# ---------------------------------------------------------------- 编码发放器


class CodeSequence(Base):
    """MAT 编码并发安全发放器。行级锁 + UPDATE 自增，替代 SELECT MAX()+1。"""

    __tablename__ = "code_sequences"

    name: Mapped[str] = mapped_column(VARCHAR(32), primary_key=True)
    current_value: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6), onupdate=func.now(6)
    )

    __table_args__ = ({"comment": "业务编码发号器（MAT-xxxxxx 等）"},)


# ---------------------------------------------------------------- 维护记录


class ActivityLog(Base):
    """统一维护记录。真正归属依据是 target_type + target_id；design_package_id 可空。"""

    __tablename__ = "activity_logs"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)

    design_package_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("design_packages.id", ondelete="SET NULL"), nullable=True
    )

    target_type: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    target_id: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)

    actor: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    action: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    before_value: Mapped[str | None] = mapped_column("before", Text, nullable=True)
    after_value: Mapped[str | None] = mapped_column("after", Text, nullable=True)

    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        CheckConstraint(
            "target_type IN ('DESIGN_PACKAGE','MATERIAL','MATERIAL_VARIANT','DERIVATIVE_BATCH',"
            "'DISTRIBUTION','PARENT_ASIN','CHILD_ASIN','UPLOAD','ASSET','ORDER_ITEM')",
            name="ck_activity_logs_target_type",
        ),
        Index("ix_activity_logs_pkg_created", "design_package_id", "created_at"),
        Index("ix_activity_logs_target_created", "target_type", "target_id", "created_at"),
        {"comment": "统一维护记录"},
    )


# ================================================================ Phase 2
#
# 副素材链路：上传的副图 Asset → 按同名 pairKey 配对 → 人工确认 → 生成上架版本
# （主副素材关系只由同名 pairKey 决定，不使用任何相似度判断）
#   derivative_batches     上架版本（V1 / V2 …），版本号唯一事实来源
#   material_variants      「设计包位置 × 版本」这一格副素材
#   variant_revisions      这一格副素材的 JPG 修订历史（旧版本永不覆盖）
#   material_pairings      某次上传里「位置 ↔ 上传副图」的配对与人工确认


# ---------------------------------------------------------------- 上架版本


class DerivativeBatch(Base):
    """
    上架版本（V1 / V2 / V3 …）。

    版本号**只能**从这里获取：禁止每个 MaterialVariant 自己算 MAX(version)+1，
    否则并发确认时会出现两个 V2。
    """

    __tablename__ = "derivative_batches"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)

    design_package_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("design_packages.id", ondelete="CASCADE"), nullable=False
    )

    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(VARCHAR(16), nullable=False)

    # 这一版是因为哪一次上传产生的（可追溯到上传记录）
    created_from_upload_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("package_uploads.id", ondelete="SET NULL"), nullable=True
    )

    # 建版当时的主素材数量：后续主素材增减不影响已建版本的语义
    main_material_count_at_creation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    created_by: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    created_at: Mapped[datetime] = _ts()

    variants: Mapped[list["MaterialVariant"]] = relationship(back_populates="batch")

    __table_args__ = (
        UniqueConstraint("design_package_id", "version_no", name="uq_batches_pkg_version"),
        CheckConstraint("version_no >= 1", name="ck_batches_version_positive"),
        Index("ix_batches_pkg_created", "design_package_id", "created_at"),
        {"comment": "上架版本 V1/V2/V3（版本号唯一事实来源）"},
    )


# ---------------------------------------------------------------- 副素材


class MaterialVariant(Base):
    """
    「设计包位置 × 上架版本」这一格的副素材。例如 位置1 + V1 → display_code = 1-1。

    display_code 只做展示：**不做全局 UNIQUE**（不同设计包、不同位置都可能出现 1-1）。
    真实唯一性是 UNIQUE(batch_id, design_package_material_id)。
    """

    __tablename__ = "material_variants"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)

    material_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("materials.id", ondelete="RESTRICT"), nullable=False
    )
    design_package_material_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("design_package_materials.id", ondelete="CASCADE"), nullable=False
    )
    batch_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("derivative_batches.id", ondelete="CASCADE"), nullable=False
    )

    display_code: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)

    # 循环 FK：由 Alembic 在建表后 ALTER TABLE 补上
    current_revision_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)

    # 副素材标签：建版时从所属主素材复制（创建时继承），之后独立修改
    tags: Mapped[list | None] = mapped_column(JSON, nullable=True)

    # BLAKE3 完全相同的另一张副素材（复用提示用，不擅自合并业务版本）
    duplicate_of_variant_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("material_variants.id", ondelete="SET NULL"), nullable=True
    )

    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6), onupdate=func.now(6)
    )

    batch: Mapped[DerivativeBatch] = relationship(back_populates="variants")

    __table_args__ = (
        UniqueConstraint("batch_id", "design_package_material_id", name="uq_variants_batch_position"),
        Index("ix_variants_material", "material_id"),
        Index("ix_variants_dpm", "design_package_material_id"),
        {"comment": "副素材：设计包位置 × 上架版本"},
    )


class VariantRevision(Base):
    """副素材实际 JPG 的修订历史。Revision 1 是第一张；旧 Revision 永不覆盖。"""

    __tablename__ = "variant_revisions"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    variant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("material_variants.id", ondelete="CASCADE"), nullable=False
    )

    revision_no: Mapped[int] = mapped_column(Integer, nullable=False)
    asset_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )

    created_by: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    created_at: Mapped[datetime] = _ts()

    deleted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        UniqueConstraint("variant_id", "revision_no", name="uq_variant_revisions_no"),
        CheckConstraint("revision_no >= 1", name="ck_variant_revisions_no_positive"),
        Index("ix_variant_revisions_asset", "asset_id"),
        {"comment": "副素材 JPG 修订历史"},
    )


class MaterialPairing(Base):
    """
    一次上传里「某个设计包位置 ↔ 哪张上传副图」的配对结果。

    **配对只按同名 pairKey 得到，不使用任何相似度判断**（第二版规则）：
      主图 1.jpg + 副图 1.jpg + PSD 1.psd → pair_key = "1" → 位置1 ↔ 副图1

    source  : NAME（按同名自动配对）/ MANUAL（人工改成别的副图）
    status  : PAIRED（已配对，待确认）/ UNPAIRED（缺副图）/ CONFIRMED（人工确认）
    variant_asset_id 指向**上传的副图 Asset**；建版后 variant_id 指向真实副素材实体。
    """

    __tablename__ = "material_pairings"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)

    design_package_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("design_packages.id", ondelete="CASCADE"), nullable=False
    )
    package_upload_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("package_uploads.id", ondelete="CASCADE"), nullable=False
    )
    design_package_material_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("design_package_materials.id", ondelete="CASCADE"), nullable=False
    )

    # 本次上传里配对给这个位置的副图 Asset（未配对上时为 NULL）
    variant_asset_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    # 该位置的 pairKey（来自主图文件名；仅本设计包内有效，不是永久身份）
    pair_key: Mapped[str] = mapped_column(VARCHAR(255), nullable=False, default="")

    source: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="NAME")
    status: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="UNPAIRED")

    # 同 pairKey 的 PSD（PSD 与主素材的关联，单独记录便于展示与追溯）
    psd_asset_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )

    # 建版后指向真实副素材实体
    variant_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("material_variants.id", ondelete="SET NULL"), nullable=True
    )

    confirmed_by: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DATETIME(fsp=6), nullable=True)

    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6), onupdate=func.now(6)
    )

    __table_args__ = (
        # 一次上传里，一个设计包位置只有一条配对
        UniqueConstraint(
            "package_upload_id",
            "design_package_material_id",
            name="uq_pairings_upload_position",
        ),
        # 一个副图只能属于一个主素材（同一上传内）
        UniqueConstraint(
            "package_upload_id", "variant_asset_id", name="uq_pairings_upload_variant"
        ),
        CheckConstraint("source IN ('NAME','MANUAL')", name="ck_pairings_source"),
        CheckConstraint(
            "status IN ('PAIRED','UNPAIRED','CONFIRMED')", name="ck_pairings_status"
        ),
        Index("ix_pairings_pkg_status", "design_package_id", "status"),
        Index("ix_pairings_upload", "package_upload_id"),
        {"comment": "主素材位置 ↔ 上传副图 的配对（按同名 pairKey，无相似度）"},
    )


class AssetImageEmbedding(Base):
    """图片搜索向量（asset_id → embedding）。检索用，**不代表业务身份**。

    同一 Asset 被多个业务对象引用时只算一次；向量不当成 Material 身份。
    索引失败不阻断素材入库（记录 IMAGE_INDEX_FAILED），之后可重建。
    """

    __tablename__ = "asset_image_embeddings"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    asset_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="CASCADE"), nullable=False
    )
    embedding_model: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    embedding_version: Mapped[int] = mapped_column(Integer, nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    # float32 小端字节（numpy 原生），比 JSON 数组紧凑
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    indexed_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("asset_id", name="uq_asset_image_embeddings_asset"),
        Index("ix_asset_image_embeddings_model", "embedding_model", "embedding_version"),
        Index("ix_asset_image_embeddings_indexed_at", "indexed_at"),
        {"comment": "图片搜索向量索引（检索用，不代表业务身份）"},
    )


# ================================================================ 订单识别 Phase A-D


class OrderImportBatch(Base):
    """一次领星订单 ZIP 导入。原始 ZIP 永久保留在 Asset 中，重复 ZIP 只标记为重复。"""

    __tablename__ = "order_import_batches"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    original_filename: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    raw_zip_asset_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    zip_blake3: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    category_code: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, default="UNKNOWN")
    category_name: Mapped[str] = mapped_column(VARCHAR(128), nullable=False, default="未知品类")
    category_source: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="UNKNOWN")
    category_confidence: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="0")
    status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False, default="PARSED")
    duplicate_of_batch_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("order_import_batches.id", ondelete="SET NULL"), nullable=True
    )
    total_json_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_by: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(
        DATETIME(fsp=6), nullable=False, server_default=func.now(6), onupdate=func.now(6)
    )

    __table_args__ = (
        Index("ix_order_import_batches_zip_hash", "zip_blake3"),
        Index("ix_order_import_batches_created", "created_at"),
        CheckConstraint(
            "status IN ('PARSED','PARTIAL','FAILED','DUPLICATE')",
            name="ck_order_import_batches_status",
        ),
        {"comment": "订单原始 ZIP 导入批次（原始文件不可丢弃）"},
    )


class OrderItem(Base):
    """跨导入批次去重后的规范化订单商品行；dedupe_key 是销量去重事实键。"""

    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    first_import_batch_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_import_batches.id", ondelete="RESTRICT"), nullable=False
    )
    order_id: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    order_item_id: Mapped[str | None] = mapped_column(VARCHAR(128), nullable=True)
    child_asin: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    sku: Mapped[str | None] = mapped_column(VARCHAR(255), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    category_code: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, default="UNKNOWN")
    category_name: Mapped[str] = mapped_column(VARCHAR(128), nullable=False, default="未知品类")
    category_source: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="UNKNOWN")
    category_confidence: Mapped[str] = mapped_column(VARCHAR(16), nullable=False, default="0")
    parser_version: Mapped[str] = mapped_column(VARCHAR(64), nullable=False, default="GENERIC_V1")
    parsed_at: Mapped[datetime] = _ts()
    raw_json_hash: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    normalized_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    matched_variant_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    matched_material_id: Mapped[str | None] = mapped_column(VARCHAR(64), nullable=True)
    match_method: Mapped[str | None] = mapped_column(VARCHAR(32), nullable=True)
    match_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    match_status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False, default="UNMATCHED")
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="uq_order_items_dedupe_key"),
        Index("ix_order_items_order", "order_id"),
        Index("ix_order_items_asin", "child_asin"),
        Index("ix_order_items_category", "category_code"),
        CheckConstraint("quantity >= 0", name="ck_order_items_quantity_nonnegative"),
        CheckConstraint(
            "match_status IN ('UNMATCHED','REVIEW_REQUIRED','CONFIRMED','FAILED')",
            name="ck_order_items_match_status",
        ),
        {"comment": "规范化订单商品行（跨重复导入去重）"},
    )


class OrderImportBatchItem(Base):
    """导入批次 ↔ 规范化订单行，保留该次导入对应的原始 JSON 与解析快照。"""

    __tablename__ = "order_import_batch_items"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    import_batch_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_import_batches.id", ondelete="CASCADE"), nullable=False
    )
    order_item_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False
    )
    raw_json_asset_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="RESTRICT"), nullable=False
    )
    raw_json_path: Mapped[str] = mapped_column(VARCHAR(1024), nullable=False)
    raw_json_hash: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    parser_version: Mapped[str] = mapped_column(VARCHAR(64), nullable=False)
    parse_status: Mapped[str] = mapped_column(VARCHAR(24), nullable=False)
    normalized_payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("import_batch_id", "order_item_id", name="uq_order_import_batch_item"),
        Index("ix_order_import_batch_items_batch", "import_batch_id"),
        CheckConstraint(
            "parse_status IN ('PARSED','REVIEW_REQUIRED','FAILED')",
            name="ck_order_import_batch_items_status",
        ),
        {"comment": "每次导入的原始 JSON 与规范化解析快照"},
    )


class OrderBuyerAsset(Base):
    """买家上传 Logo 等订单生产附件；永远不创建 Material / Variant。"""

    __tablename__ = "order_buyer_assets"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    order_item_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("order_items.id", ondelete="CASCADE"), nullable=False
    )
    asset_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    role: Mapped[str] = mapped_column(VARCHAR(24), nullable=False)
    source_url: Mapped[str | None] = mapped_column(VARCHAR(2048), nullable=True)
    source_path: Mapped[str | None] = mapped_column(VARCHAR(1024), nullable=True)
    is_primary: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        Index("ix_order_buyer_assets_item", "order_item_id"),
        CheckConstraint(
            "role IN ('BUYER_LOGO_ORIGINAL','BUYER_LOGO_SVG')",
            name="ck_order_buyer_assets_role",
        ),
        {"comment": "订单买家附件，不属于素材库业务实体"},
    )


# ================================================================ 订单识别 Phase E-G：素材归因绑定


class MaterialUrlBinding(Base):
    """买家订单里的素材 URL → Variant 的绑定（人工确认后建立）。

    MATERIAL_SOURCE 匹配时：URL 命中绑定 → 直接 URL → Variant → MAT，不再跑图片匹配。
    相同 URL 以后直接复用。
    """

    __tablename__ = "material_url_bindings"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    # 唯一索引受 utf8mb4 3072 字节限制：URL 截到 512 字符足够去重
    material_url: Mapped[str] = mapped_column(VARCHAR(512), nullable=False)
    variant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("material_variants.id", ondelete="CASCADE"), nullable=False
    )
    material_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("materials.id", ondelete="SET NULL"), nullable=True
    )
    match_method: Mapped[str] = mapped_column(VARCHAR(32), nullable=False, default="MANUAL")
    created_by: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("material_url", name="uq_material_url_bindings_url"),
        Index("ix_material_url_bindings_variant", "variant_id"),
        {"comment": "买家素材 URL → Variant 绑定（人工确认后建立，URL 直接复用）"},
    )


class AsinVariantBinding(Base):
    """Child ASIN → Variant 绑定（审核确认时建立）。

    现有系统后端没有 Distribution（派发）表，ASIN→Batch→Variant 的候选定位
    用这张绑定表替代：确认某个 ASIN 对应哪个 Variant 后，相同 ASIN 的订单
    直接在该 Variant 候选内匹配，不与整个素材库比较。
    """

    __tablename__ = "asin_variant_bindings"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    child_asin: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    variant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("material_variants.id", ondelete="CASCADE"), nullable=False
    )
    material_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("materials.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("child_asin", "variant_id", name="uq_asin_variant_bindings_pair"),
        Index("ix_asin_variant_bindings_asin", "child_asin"),
        {"comment": "Child ASIN → Variant 绑定（替代不存在的 Distribution 表定位候选）"},
    )


class VariantEffectImage(Base):
    """Variant 的最终效果图（FINAL_EFFECT）等图片角色。

    现有 VariantRevision 只存素材源图（MATERIAL_SOURCE）；FINAL_EFFECT / Black /
    White 效果图走这张表，优先复用已有 Asset（同一物理文件不重复创建）。
    """

    __tablename__ = "variant_effect_images"

    id: Mapped[str] = mapped_column(VARCHAR(64), primary_key=True)
    variant_id: Mapped[str] = mapped_column(
        VARCHAR(64), ForeignKey("material_variants.id", ondelete="CASCADE"), nullable=False
    )
    image_role: Mapped[str] = mapped_column(VARCHAR(32), nullable=False)
    sole_color: Mapped[str | None] = mapped_column(VARCHAR(16), nullable=True)
    asset_id: Mapped[str | None] = mapped_column(
        VARCHAR(64), ForeignKey("assets.id", ondelete="SET NULL"), nullable=True
    )
    source_url: Mapped[str | None] = mapped_column(VARCHAR(2048), nullable=True)
    created_by: Mapped[str] = mapped_column(VARCHAR(128), nullable=False)
    created_at: Mapped[datetime] = _ts()

    __table_args__ = (
        UniqueConstraint("variant_id", "image_role", "sole_color", name="uq_variant_effect_role"),
        CheckConstraint(
            "image_role IN ('MATERIAL_SOURCE','FINAL_EFFECT','PREVIEW_ONLY')",
            name="ck_variant_effect_images_role",
        ),
        {"comment": "Variant 图片角色（MATERIAL_SOURCE / FINAL_EFFECT Black/White）"},
    )
