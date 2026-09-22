from datetime import timedelta
from unittest.mock import MagicMock

from django.contrib.auth.models import User
from django.db import models
from django.db.transaction import get_connection
from django.utils import timezone
import reversion
from reversion.models import Version
from test_app.models import TestModel, TestModelRelated, TestModelThrough, TestModelParent, TestMeta
from test_app.tests.base import TestBase, TestBaseTransaction, TestModelMixin, UserMixin


class SaveTest(TestModelMixin, TestBase):

    def testModelSave(self):
        TestModel.objects.create()
        self.assertNoRevision()


class IsRegisteredTest(TestModelMixin, TestBase):

    def testIsRegistered(self):
        self.assertTrue(reversion.is_registered(TestModel))


class IsRegisterUnregisteredTest(TestBase):

    def testIsRegisteredFalse(self):
        self.assertFalse(reversion.is_registered(TestModel))


class GetRegisteredModelsTest(TestModelMixin, TestBase):

    def testGetRegisteredModels(self):
        self.assertEqual(set(reversion.get_registered_models()), {TestModel})


class RegisterTest(TestBase):

    def testRegister(self):
        reversion.register(TestModel)
        self.assertTrue(reversion.is_registered(TestModel))

    def testRegisterDecorator(self):
        @reversion.register()
        class TestModelDecorater(models.Model):
            pass
        self.assertTrue(reversion.is_registered(TestModelDecorater))

    def testRegisterAlreadyRegistered(self):
        reversion.register(TestModel)
        with self.assertRaises(reversion.RegistrationError):
            reversion.register(TestModel)

    def testRegisterM2MSThroughLazy(self):
        # When register is used as a decorator in models.py, lazy relations haven't had a chance to be resolved, so
        # will still be a string.
        @reversion.register()
        class TestModelLazy(models.Model):
            related = models.ManyToManyField(
                TestModelRelated,
                through="TestModelThroughLazy",
            )

        class TestModelThroughLazy(models.Model):
            pass


class UnregisterTest(TestModelMixin, TestBase):

    def testUnregister(self):
        reversion.unregister(TestModel)
        self.assertFalse(reversion.is_registered(TestModel))


class UnregisterUnregisteredTest(TestBase):

    def testUnregisterNotRegistered(self):
        with self.assertRaises(reversion.RegistrationError):
            reversion.unregister(User)


class CreateRevisionTest(TestModelMixin, TestBase):

    def testCreateRevision(self):
        with reversion.create_revision():
            obj = TestModel.objects.create()
        self.assertSingleRevision((obj,))

    def testCreateRevisionNested(self):
        with reversion.create_revision():
            with reversion.create_revision():
                obj = TestModel.objects.create()
        self.assertSingleRevision((obj,))

    def testCreateRevisionEmpty(self):
        with reversion.create_revision():
            pass
        self.assertNoRevision()

    def testCreateRevisionException(self):
        try:
            with reversion.create_revision():
                TestModel.objects.create()
                raise Exception("Boom!")
        except Exception:
            pass
        self.assertNoRevision()

    def testCreateRevisionDecorator(self):
        obj = reversion.create_revision()(TestModel.objects.create)()
        self.assertSingleRevision((obj,))

    def testPreRevisionCommitSignal(self):
        _callback = MagicMock()
        reversion.signals.pre_revision_commit.connect(_callback)

        with reversion.create_revision():
            TestModel.objects.create()
        self.assertEqual(_callback.call_count, 1)

    def testPostRevisionCommitSignal(self):
        _callback = MagicMock()
        reversion.signals.post_revision_commit.connect(_callback)

        with reversion.create_revision():
            TestModel.objects.create()
        self.assertEqual(_callback.call_count, 1)


class CreateRevisionAtomicTest(TestModelMixin, TestBaseTransaction):
    def testCreateRevisionAtomic(self):
        self.assertFalse(get_connection().in_atomic_block)
        with reversion.create_revision():
            self.assertTrue(get_connection().in_atomic_block)

    def testCreateRevisionNonAtomic(self):
        self.assertFalse(get_connection().in_atomic_block)
        with reversion.create_revision(atomic=False):
            self.assertFalse(get_connection().in_atomic_block)

    def testCreateRevisionInOnCommitHandler(self):
        from django.db import transaction
        from reversion.models import Revision

        self.assertEqual(Revision.objects.all().count(), 0)

        with reversion.create_revision(atomic=True):
            model = TestModel.objects.create()

            def on_commit():
                with reversion.create_revision(atomic=True):
                    model.name = 'oncommit'
                    model.save()

            transaction.on_commit(on_commit)

        self.assertEqual(Revision.objects.all().count(), 2)


class CreateRevisionManageManuallyTest(TestModelMixin, TestBase):

    def testCreateRevisionManageManually(self):
        with reversion.create_revision(manage_manually=True):
            TestModel.objects.create()
        self.assertNoRevision()

    def testCreateRevisionManageManuallyNested(self):
        with reversion.create_revision():
            with reversion.create_revision(manage_manually=True):
                TestModel.objects.create()
        self.assertNoRevision()


class CreateRevisionDbTest(TestModelMixin, TestBase):
    databases = {"default", "mysql", "postgres"}

    def testCreateRevisionMultiDb(self):
        with reversion.create_revision(using="mysql"), reversion.create_revision(using="postgres"):
            obj = TestModel.objects.create()
        self.assertNoRevision()
        self.assertSingleRevision((obj,), using="mysql")
        self.assertSingleRevision((obj,), using="postgres")


class CreateRevisionFollowTest(TestBase):

    def testCreateRevisionFollow(self):
        reversion.register(TestModel, follow=("related",))
        reversion.register(TestModelRelated)
        obj_related = TestModelRelated.objects.create()
        with reversion.create_revision():
            obj = TestModel.objects.create()
            obj.related.add(obj_related)
        self.assertSingleRevision((obj, obj_related))

    def testCreateRevisionFollowThrough(self):
        reversion.register(TestModel, follow=("related_through",))
        reversion.register(TestModelThrough, follow=("test_model", "test_model_related",))
        reversion.register(TestModelRelated)
        obj_related = TestModelRelated.objects.create()
        with reversion.create_revision():
            obj = TestModel.objects.create()
            obj_through = TestModelThrough.objects.create(
                test_model=obj,
                test_model_related=obj_related,
            )
        self.assertSingleRevision((obj, obj_through, obj_related))

    def testCreateRevisionFollowInvalid(self):
        reversion.register(TestModel, follow=("name",))
        with reversion.create_revision():
            with self.assertRaises(reversion.RegistrationError):
                TestModel.objects.create()


class CreateRevisionIgnoreDuplicatesTest(TestBase):

    def testCreateRevisionIgnoreDuplicates(self):
        reversion.register(TestModel, ignore_duplicates=True)
        with reversion.create_revision():
            obj = TestModel.objects.create()
        with reversion.create_revision():
            obj.save()
        self.assertSingleRevision((obj,))


class CreateRevisionBulkOperationTest(TestModelMixin, TestBase):

    def testCreateRevisionUpdate(self):
        with reversion.create_revision():
            obj = TestModel.objects.create()
        with reversion.create_revision():
            TestModel.objects.filter(pk=obj.pk).update(name="v2")
        versions = Version.objects.get_for_object_reference(TestModel, obj.pk)
        self.assertEqual(versions.count(), 2)
        self.assertEqual(versions[0].field_dict["name"], "v2")

    def testCreateRevisionUpdateNoMatches(self):
        with reversion.create_revision():
            rows_updated = TestModel.objects.filter(pk=-1).update(name="v2")
        self.assertEqual(rows_updated, 0)
        self.assertNoRevision()

    def testCreateRevisionUpdateMultiple(self):
        with reversion.create_revision():
            obj_1 = TestModel.objects.create()
            obj_2 = TestModel.objects.create()
        with reversion.create_revision():
            TestModel.objects.filter(pk__in=[obj_1.pk, obj_2.pk]).update(name="v2")
        self.assertEqual(Version.objects.get_for_object_reference(TestModel, obj_1.pk)[0].field_dict["name"], "v2")
        self.assertEqual(Version.objects.get_for_object_reference(TestModel, obj_2.pk)[0].field_dict["name"], "v2")

    def testCreateRevisionBulkUpdate(self):
        with reversion.create_revision():
            obj = TestModel.objects.create()
        obj.name = "v2"
        with reversion.create_revision():
            TestModel.objects.bulk_update([obj], ["name"])
        versions = Version.objects.get_for_object_reference(TestModel, obj.pk)
        self.assertEqual(versions.count(), 2)
        self.assertEqual(versions[0].field_dict["name"], "v2")

    def testCreateRevisionBulkUpdateNoObjects(self):
        with reversion.create_revision():
            rows_updated = TestModel.objects.bulk_update([], ["name"])
        self.assertEqual(rows_updated, 0)
        self.assertNoRevision()

    def testCreateRevisionBulkUpdateMultiple(self):
        with reversion.create_revision():
            obj_1 = TestModel.objects.create()
            obj_2 = TestModel.objects.create()
        obj_1.name = "v2"
        obj_2.name = "v3"
        with reversion.create_revision():
            TestModel.objects.bulk_update([obj_1, obj_2], ["name"])
        self.assertEqual(Version.objects.get_for_object_reference(TestModel, obj_1.pk)[0].field_dict["name"], "v2")
        self.assertEqual(Version.objects.get_for_object_reference(TestModel, obj_2.pk)[0].field_dict["name"], "v3")

    def testCreateRevisionBulkUpdateDuplicateObject(self):
        with reversion.create_revision():
            obj = TestModel.objects.create()
        obj_first = TestModel.objects.get(pk=obj.pk)
        obj_second = TestModel.objects.get(pk=obj.pk)
        obj_first.name = "v2"
        obj_second.name = "v3"
        with reversion.create_revision():
            TestModel.objects.bulk_update([obj_first, obj_second], ["name"])
        obj.refresh_from_db()
        self.assertEqual(obj.name, "v2")
        self.assertEqual(Version.objects.get_for_object_reference(TestModel, obj.pk)[0].field_dict["name"], "v2")

    def testCreateRevisionBulkUpdateFilteredQuerySet(self):
        with reversion.create_revision():
            obj_1 = TestModel.objects.create()
            obj_2 = TestModel.objects.create()
        obj_1.name = "v2"
        obj_2.name = "v3"
        with reversion.create_revision():
            rows_updated = TestModel.objects.filter(pk=obj_1.pk).bulk_update([obj_1, obj_2], ["name"])
        self.assertEqual(rows_updated, 1)
        obj_1.refresh_from_db()
        obj_2.refresh_from_db()
        self.assertEqual(obj_1.name, "v2")
        self.assertEqual(obj_2.name, "v1")
        self.assertEqual(Version.objects.get_for_object_reference(TestModel, obj_1.pk).count(), 2)
        self.assertEqual(Version.objects.get_for_object_reference(TestModel, obj_2.pk).count(), 1)

    def testCreateRevisionBulkDelete(self):
        with reversion.create_revision():
            obj = TestModel.objects.create()
        with reversion.create_revision():
            TestModel.objects.filter(pk=obj.pk).delete()
        deleted = Version.objects.get_deleted(TestModel)
        self.assertEqual(deleted.count(), 1)
        self.assertEqual(deleted.get().object_id, str(obj.pk))


class CreateRevisionInheritanceTest(TestModelMixin, TestBase):

    def testCreateRevisionInheritance(self):
        reversion.register(TestModelParent, follow=("testmodel_ptr",))
        with reversion.create_revision():
            obj = TestModelParent.objects.create()
        self.assertSingleRevision((obj, obj.testmodel_ptr))


class SetCommentTest(TestModelMixin, TestBase):

    def testSetComment(self):
        with reversion.create_revision():
            reversion.set_comment("comment v1")
            obj = TestModel.objects.create()
        self.assertSingleRevision((obj,), comment="comment v1")

    def testSetCommentNoBlock(self):
        with self.assertRaises(reversion.RevisionManagementError):
            reversion.set_comment("comment v1")


class GetCommentTest(TestBase):

    def testGetComment(self):
        with reversion.create_revision():
            reversion.set_comment("comment v1")
            self.assertEqual(reversion.get_comment(), "comment v1")

    def testGetCommentDefault(self):
        with reversion.create_revision():
            self.assertEqual(reversion.get_comment(), "")

    def testGetCommentNoBlock(self):
        with self.assertRaises(reversion.RevisionManagementError):
            reversion.get_comment()


class SetUserTest(UserMixin, TestModelMixin, TestBase):

    def testSetUser(self):
        with reversion.create_revision():
            reversion.set_user(self.user)
            obj = TestModel.objects.create()
        self.assertSingleRevision((obj,), user=self.user)

    def testSetUserNoBlock(self):
        with self.assertRaises(reversion.RevisionManagementError):
            reversion.set_user(self.user)


class GetUserTest(UserMixin, TestBase):

    def testGetUser(self):
        with reversion.create_revision():
            reversion.set_user(self.user)
            self.assertEqual(reversion.get_user(), self.user)

    def testGetUserDefault(self):
        with reversion.create_revision():
            self.assertEqual(reversion.get_user(), None)

    def testGetUserNoBlock(self):
        with self.assertRaises(reversion.RevisionManagementError):
            reversion.get_user()


class SetDateCreatedTest(TestModelMixin, TestBase):

    def testSetDateCreated(self):
        date_created = timezone.now() - timedelta(days=20)
        with reversion.create_revision():
            reversion.set_date_created(date_created)
            obj = TestModel.objects.create()
        self.assertSingleRevision((obj,), date_created=date_created)

    def testDateCreatedNoBlock(self):
        with self.assertRaises(reversion.RevisionManagementError):
            reversion.set_date_created(timezone.now())


class GetDateCreatedTest(TestBase):

    def testGetDateCreated(self):
        date_created = timezone.now() - timedelta(days=20)
        with reversion.create_revision():
            reversion.set_date_created(date_created)
            self.assertEqual(reversion.get_date_created(), date_created)

    def testGetDateCreatedDefault(self):
        with reversion.create_revision():
            self.assertAlmostEqual(reversion.get_date_created(), timezone.now(), delta=timedelta(seconds=1))

    def testGetDateCreatedNoBlock(self):
        with self.assertRaises(reversion.RevisionManagementError):
            reversion.get_date_created()


class AddMetaTest(TestModelMixin, TestBase):
    databases = {"default", "mysql", "postgres"}

    def testAddMeta(self):
        with reversion.create_revision():
            reversion.add_meta(TestMeta, name="meta v1")
            obj = TestModel.objects.create()
        self.assertSingleRevision((obj,), meta_names=("meta v1",))

    def testAddMetaNoBlock(self):
        with self.assertRaises(reversion.RevisionManagementError):
            reversion.add_meta(TestMeta, name="meta v1")

    def testAddMetaMultDb(self):
        with reversion.create_revision(using="mysql"), reversion.create_revision(using="postgres"):
            obj = TestModel.objects.create()
            reversion.add_meta(TestMeta, name="meta v1")
        self.assertNoRevision()
        self.assertSingleRevision((obj,), meta_names=("meta v1",), using="mysql")
        self.assertSingleRevision((obj,), meta_names=("meta v1",), using="postgres")
